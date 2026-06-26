"""In-memory revocation list with TTL entries and propagation support.

The :class:`RevocationList` is the local half of the revocation system.  It
tracks which tokens and certificates have been revoked and answers ``is_revoked``
queries from :func:`~proxion_messenger_core.validator.validate_request`.

Revocation IDs
--------------
Each entry in the list is keyed by a *revocation ID* — a deterministic hash
derived from the object being revoked:

* **Tokens**: ``SHA-256(canonical_json_of_token_payload)`` — computed by
  :func:`token_revocation_id`.  Using the full payload rather than just the
  ``token_id`` ensures the ID is unguessable without knowing the token
  structure; an attacker who only knows the token ID cannot pre-register a
  fake revocation.

* **Certificates**: ``SHA-256("cert:" + certificate_id)`` — computed by
  :func:`certificate_revocation_id`.  The ``"cert:"`` prefix avoids any
  namespace collision with token IDs.

Local vs. propagated revocations
---------------------------------
* **Local revocations** are added via :meth:`RevocationList.revoke` (passing a
  :class:`~proxion_messenger_core.tokens.Token` object).  The revocation ID is derived
  from the full token payload.
* **Propagated revocations** arrive as
  :class:`~proxion_messenger_core.revoke.RevocationNotice` messages through the
  Coordination Store.  The notice carries the pre-computed revocation ID, which
  is added directly via :meth:`RevocationList.revoke_until`.  When the
  verifier later encounters a token and calls :meth:`RevocationList.is_revoked`,
  it re-derives the same ID from the token payload — and finds the entry.

TTL / expiry
------------
Every revocation entry stores a ``revoked_until`` timestamp.  Entries are
cleaned up lazily (on ``is_revoked`` queries) and eagerly (via
:meth:`RevocationList.purge`).  The ``revoked_until`` is normally set to the
token's own ``exp`` — there is no point keeping a revocation entry after the
token would have expired naturally.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import threading
from typing import TYPE_CHECKING, Dict, Optional, Union

from .tokens import Token, token_canonical_bytes

if TYPE_CHECKING:
    # Imported only for type hints — avoids a circular dependency at runtime.
    from .federation import RelationshipCertificate


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _coerce_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hash_token_bytes(token_bytes: bytes) -> str:
    return hashlib.sha256(token_bytes).hexdigest()


def _derive_revocation_id(token: Token) -> str:
    """Internal: derive the revocation list key for a token."""
    return _hash_token_bytes(token_canonical_bytes(token))


# ---------------------------------------------------------------------------
# Public ID derivation functions
# ---------------------------------------------------------------------------

def token_revocation_id(token: Token) -> str:
    """Return the canonical revocation ID for *token*.

    This is the key used inside :class:`RevocationList` for token entries.
    Include it in a :class:`~proxion_messenger_core.revoke.RevocationNotice` so that
    peers can add it to their own lists via
    :meth:`RevocationList.revoke_until` without needing the full token object.

    Parameters
    ----------
    token:
        The token to derive an ID for.

    Returns
    -------
    str
        Hex-encoded SHA-256 of the token's canonical JSON payload.
    """
    return _derive_revocation_id(token)


def certificate_revocation_id(cert: "RelationshipCertificate") -> str:
    """Return the canonical revocation ID for a :class:`~proxion_messenger_core.federation.RelationshipCertificate`.

    The ``"cert:"`` prefix ensures no namespace collision with token IDs even
    if a certificate's UUID were somehow identical to a token's canonical hash.

    Parameters
    ----------
    cert:
        The certificate to derive an ID for.

    Returns
    -------
    str
        Hex-encoded SHA-256 of ``"cert:" + certificate_id``.
    """
    raw = ("cert:" + cert.certificate_id).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class RevocationEntry:
    revoked_until: datetime


class RevocationList:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: Dict[str, RevocationEntry] = {}

    def __len__(self) -> int:
        """Return the number of active revocation entries."""
        with self._lock:
            return len(self._entries)

    def revoke_until(self, revocation_id: str, until: datetime) -> None:
        """Add a pre-computed revocation ID with an explicit expiry time.

        This is the primitive used by the revocation propagation layer.  When a
        :class:`~proxion_messenger_core.revoke.RevocationNotice` arrives through the
        Coordination Store, it carries the ``revocation_id`` (already computed
        by the original issuer) and the ``not_after`` timestamp (the token's
        own expiry).  Call this method to register the revocation locally
        without needing the full token object.

        Parameters
        ----------
        revocation_id:
            The hex revocation ID from a
            :class:`~proxion_messenger_core.revoke.RevocationNotice` — either
            :func:`token_revocation_id` or :func:`certificate_revocation_id`
            output.
        until:
            The datetime after which this revocation entry can be discarded
            (i.e. the token's or certificate's natural expiry).

        Notes
        -----
        Subsequent calls to :meth:`is_revoked` with the corresponding
        :class:`~proxion_messenger_core.tokens.Token` object will re-derive the same
        ``revocation_id`` and find this entry — so the token will correctly
        appear revoked even though :meth:`revoke` was never called with the
        full token.
        """
        until_dt = _coerce_datetime(until)
        with self._lock:
            self._entries[revocation_id] = RevocationEntry(revoked_until=until_dt)

    def revoke(
        self,
        token_or_token_id: Union[Token, str],
        now: datetime,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        now_dt = _coerce_datetime(now)
        token_id, token_exp = self._resolve_token(token_or_token_id)
        if ttl_seconds is None:
            if token_exp is None:
                raise ValueError("ttl_seconds required when token expiration is unknown")
            revoked_until = token_exp
        else:
            if ttl_seconds <= 0:
                raise ValueError("ttl_seconds must be positive")
            revoked_until = now_dt + timedelta(seconds=ttl_seconds)
            if token_exp is not None and token_exp < revoked_until:
                revoked_until = token_exp
        with self._lock:
            self._entries[token_id] = RevocationEntry(revoked_until=revoked_until)
        return token_id

    def is_revoked(self, token_or_token_id: Union[Token, str], now: datetime) -> bool:
        now_dt = _coerce_datetime(now)
        token_id, _ = self._resolve_token(token_or_token_id)
        with self._lock:
            entry = self._entries.get(token_id)
            if entry is None:
                return False
            if now_dt >= _coerce_datetime(entry.revoked_until):
                del self._entries[token_id]
                return False
            return True

    def purge(self, now: datetime) -> int:
        now_dt = _coerce_datetime(now)
        removed = 0
        with self._lock:
            expired = [
                token_id
                for token_id, entry in self._entries.items()
                if now_dt >= _coerce_datetime(entry.revoked_until)
            ]
            for token_id in expired:
                del self._entries[token_id]
                removed += 1
        return removed

    def _resolve_token(self, token_or_token_id: Union[Token, str]) -> tuple[str, Optional[datetime]]:
        if isinstance(token_or_token_id, Token):
            return _derive_revocation_id(token_or_token_id), token_or_token_id.exp
        if isinstance(token_or_token_id, str):
            return token_or_token_id, None
        raise TypeError("token_or_token_id must be Token or str")

    def to_dict(self) -> dict:
        """Serialize active entries to a plain dict (ISO timestamps).

        Expired entries are excluded.  Call this just before writing to disk.
        """
        now = datetime.now(timezone.utc)
        with self._lock:
            return {
                rev_id: entry.revoked_until.isoformat()
                for rev_id, entry in self._entries.items()
                if now < _coerce_datetime(entry.revoked_until)
            }

    @classmethod
    def from_dict(cls, d: dict) -> "RevocationList":
        """Reconstruct from a dict produced by to_dict()."""
        rl = cls()
        now = datetime.now(timezone.utc)
        for rev_id, until_iso in d.items():
            until = datetime.fromisoformat(until_iso)
            if not until.tzinfo:
                until = until.replace(tzinfo=timezone.utc)
            if now < until:
                rl._entries[rev_id] = RevocationEntry(revoked_until=until)
        return rl

    def save(self, path: str) -> None:
        """Write to a JSON file atomically."""
        import json, os, tempfile
        data = self.to_dict()
        dir_ = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: str) -> "RevocationList":
        """Load from a JSON file.  Returns empty list if file missing."""
        import json, os
        if not os.path.exists(path):
            return cls()
        with open(path) as fh:
            return cls.from_dict(json.load(fh))
