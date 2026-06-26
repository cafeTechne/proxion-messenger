"""Revocation propagation through the Coordination Store.

This module is the *distributed* half of the revocation system.  The local
half — :class:`~proxion_messenger_core.revocation.RevocationList` — answers ``is_revoked``
queries.  This module produces and routes the signed
:class:`RevocationNotice` messages that populate those lists on remote peers.

Why propagation is needed
--------------------------
Without propagation, revoking a token is only effective on the node that holds
the :class:`~proxion_messenger_core.revocation.RevocationList`.  A remote resource server
that has cached the token would continue to accept it until its natural expiry.

With propagation:

1. The issuer calls :func:`revoke_and_broadcast`.
2. A :class:`RevocationNotice` is signed with the issuer's Ed25519 key and
   sealed for each peer's X25519 store key.
3. Peers call :func:`receive_revocations` to drain their mailbox, verify each
   notice's signature, and apply valid notices to their local
   :class:`~proxion_messenger_core.revocation.RevocationList` via
   :meth:`~proxion_messenger_core.revocation.RevocationList.revoke_until`.
4. On the next ``validate_request`` call, the token is denied.

Security properties
--------------------
* **Authenticity**: notices are signed by the issuer's Ed25519 key.  Peers
  only apply a notice if the signature is valid.
* **Freshness**: the ``revoked_at`` timestamp is included in the signed
  payload, preventing trivial replay of old notices.
* **Confidentiality**: notices are sealed inside
  :class:`~proxion_messenger_core.sealed.SealedEnvelope`; the store operator cannot
  learn which tokens are being revoked or by whom.
* **Scope**: a notice is only trusted if the ``issuer_pub_key`` matches the
  signer.  Callers SHOULD additionally verify that the issuer is the actual
  issuer of the token being revoked (compare against the delegation chain).

Message types
-------------
Revocation notices carry ``@type = "RevocationNotice"`` so that
:func:`receive_revocations` can filter them without consuming messages
destined for other protocol layers (handshake, certificates, etc.).

Notice subjects
---------------
Two subject types are supported, both represented by the same dataclass:

* ``"token"``       — revoke a specific capability token.  The
  ``revocation_id`` is :func:`~proxion_messenger_core.revocation.token_revocation_id`.
* ``"certificate"`` — revoke a
  :class:`~proxion_messenger_core.federation.RelationshipCertificate`.  The
  ``revocation_id`` is
  :func:`~proxion_messenger_core.revocation.certificate_revocation_id`.

Certificate revocation does **not** automatically cascade to tokens issued
under the certificate in this EI0 implementation.  Each token must be revoked
individually.  A future EI MAY implement cascade revocation by maintaining an
index of ``certificate_id → issued_token_ids``.

Latency and guarantees
-----------------------
The Coordination Store is best-effort and asynchronous.  Delivery is
guaranteed only within the store's TTL window.  Callers MUST NOT rely on
immediate consistency — there is always a window between revocation broadcast
and peer acknowledgement.  This is an explicit trade-off documented in the
Proxion spec §6.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .errors import ProxionError
from .federation import RelationshipCertificate
from .revocation import (
    RevocationList,
    certificate_revocation_id,
    token_revocation_id,
)
from .sealed import mailbox_id_for, open_sealed_json, seal_json
from .store import MemoryStore
from .tokens import Token

_MESSAGE_TYPE = "RevocationNotice"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class RevocationError(ProxionError):
    """Raised when a revocation notice is malformed or cannot be applied."""


# ---------------------------------------------------------------------------
# RevocationNotice
# ---------------------------------------------------------------------------

@dataclass
class RevocationNotice:
    """A signed, sealed message declaring that a token or certificate is revoked.

    Attributes
    ----------
    notice_id:
        Randomly generated UUID-like identifier.  Peers use this for
        idempotent processing — if the same ``notice_id`` is received twice
        (e.g. after a retry), only the first application has effect.
    subject_type:
        ``"token"`` or ``"certificate"``.  Determines how the ``revocation_id``
        was derived and what the ``subject_id`` refers to.
    subject_id:
        Human-readable identifier: ``token_id`` for tokens,
        ``certificate_id`` for certificates.  Useful for logging and
        debugging; the ``revocation_id`` hash is what gets stored.
    revocation_id:
        Deterministic hash of the revoked object:

        * tokens: ``SHA-256(canonical_json_of_token_payload)``
          from :func:`~proxion_messenger_core.revocation.token_revocation_id`.
        * certificates: ``SHA-256("cert:" + certificate_id)``
          from :func:`~proxion_messenger_core.revocation.certificate_revocation_id`.

        This is the key added to the peer's
        :class:`~proxion_messenger_core.revocation.RevocationList`.
    not_after:
        Unix timestamp (int) — the natural expiry of the revoked object.
        Peers use this as the ``until`` argument to
        :meth:`~proxion_messenger_core.revocation.RevocationList.revoke_until`, ensuring
        the revocation entry is cleaned up when the token would have expired
        anyway.
    issuer_pub_key:
        Hex-encoded raw Ed25519 public key of the revoking party.  Peers
        verify the ``signature`` against this key.
    revoked_at:
        Unix timestamp (int) when the revocation was issued.  Included in the
        signed payload to prevent replay of old notices against new tokens.
    reason:
        Optional free-text reason string (e.g. ``"key_compromise"``).  Not
        machine-checked — purely informational.
    signature:
        Hex-encoded Ed25519 signature over the canonical JSON of all other
        fields (excluding ``signature`` itself).  Produced by
        :meth:`sign` and verified by :meth:`verify`.
    """

    notice_id: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    subject_type: str = ""        # "token" or "certificate"
    subject_id: str = ""
    revocation_id: str = ""
    not_after: int = 0            # unix timestamp
    issuer_pub_key: str = ""      # hex Ed25519
    revoked_at: int = field(default_factory=lambda: int(time.time()))
    reason: Optional[str] = None
    signature: Optional[str] = None

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dict (suitable for sealing and transport)."""
        return {
            "@type": _MESSAGE_TYPE,
            "notice_id": self.notice_id,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "revocation_id": self.revocation_id,
            "not_after": self.not_after,
            "issuer_pub_key": self.issuer_pub_key,
            "revoked_at": self.revoked_at,
            "reason": self.reason,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RevocationNotice":
        """Reconstruct from a deserialised dict."""
        return cls(
            notice_id=d["notice_id"],
            subject_type=d["subject_type"],
            subject_id=d["subject_id"],
            revocation_id=d["revocation_id"],
            not_after=d["not_after"],
            issuer_pub_key=d["issuer_pub_key"],
            revoked_at=d.get("revoked_at", int(time.time())),
            reason=d.get("reason"),
            signature=d.get("signature"),
        )

    # ------------------------------------------------------------------
    # Signing and verification
    # ------------------------------------------------------------------

    def _canonical_payload(self) -> bytes:
        """Canonical JSON of all fields except ``signature``.

        Deterministic (sorted keys, compact separators) — the same bytes will
        be produced on any conforming implementation for the same notice.
        """
        payload = self.to_dict()
        payload.pop("signature", None)
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def sign(self, issuer_priv: Ed25519PrivateKey) -> None:
        """Sign this notice in-place with *issuer_priv*.

        Sets ``issuer_pub_key`` from the key and writes ``signature``.
        Call this after constructing the notice and before broadcasting it.
        """
        pub_bytes = issuer_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.issuer_pub_key = pub_bytes.hex()
        sig_bytes = issuer_priv.sign(self._canonical_payload())
        self.signature = sig_bytes.hex()

    def verify(self) -> bool:
        """Verify the notice's Ed25519 signature.

        Returns ``True`` if the signature is present and valid; ``False``
        otherwise.  Does **not** raise — callers should treat a ``False``
        return as a reason to discard the notice.

        Note that verifying the signature only proves the message was signed
        by the key in ``issuer_pub_key``.  Callers SHOULD additionally check
        that this key is actually the issuer of the token or certificate being
        revoked (e.g. by comparing against the delegation chain or the
        :class:`~proxion_messenger_core.federation.RelationshipCertificate` issuer
        field).
        """
        if not self.signature or not self.issuer_pub_key:
            return False
        try:
            pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(self.issuer_pub_key))
            pub.verify(bytes.fromhex(self.signature), self._canonical_payload())
            return True
        except (InvalidSignature, ValueError):
            return False


# ---------------------------------------------------------------------------
# Notice construction
# ---------------------------------------------------------------------------

def create_token_revocation(
    token: Token,
    issuer_priv: Ed25519PrivateKey,
    reason: Optional[str] = None,
) -> RevocationNotice:
    """Build and sign a :class:`RevocationNotice` for a capability token.

    Parameters
    ----------
    token:
        The token to revoke.  Its ``token_id`` and ``exp`` are embedded in
        the notice so peers can apply and later clean up the revocation.
    issuer_priv:
        The issuer's Ed25519 private key.  The notice is signed with this key;
        peers verify it against ``issuer_pub_key`` in the notice.
    reason:
        Optional free-text reason (e.g. ``"key_compromise"``,
        ``"user_requested"``).

    Returns
    -------
    RevocationNotice
        A signed notice ready to pass to :func:`broadcast_revocation`.
    """
    notice = RevocationNotice(
        subject_type="token",
        subject_id=token.token_id,
        revocation_id=token_revocation_id(token),
        not_after=int(token.exp.timestamp()),
        reason=reason,
    )
    notice.sign(issuer_priv)
    return notice


def create_certificate_revocation(
    cert: RelationshipCertificate,
    issuer_priv: Ed25519PrivateKey,
    reason: Optional[str] = None,
) -> RevocationNotice:
    """Build and sign a :class:`RevocationNotice` for a
    :class:`~proxion_messenger_core.federation.RelationshipCertificate`.

    Parameters
    ----------
    cert:
        The certificate to revoke.  Its ``certificate_id`` and ``expires_at``
        are embedded in the notice.
    issuer_priv:
        The issuer's Ed25519 private key.
    reason:
        Optional free-text reason.

    Returns
    -------
    RevocationNotice
        A signed notice ready to pass to :func:`broadcast_revocation`.

    Notes
    -----
    Certificate revocation does **not** automatically cascade to tokens issued
    under the certificate in this EI0 implementation.  Revoke each token
    individually using :func:`create_token_revocation` if cascade behaviour
    is required.
    """
    notice = RevocationNotice(
        subject_type="certificate",
        subject_id=cert.certificate_id,
        revocation_id=certificate_revocation_id(cert),
        not_after=cert.expires_at,
        reason=reason,
    )
    notice.sign(issuer_priv)
    return notice


# ---------------------------------------------------------------------------
# Broadcast / receive
# ---------------------------------------------------------------------------

def broadcast_revocation(
    notice: RevocationNotice,
    peer_store_pub_keys: Sequence[bytes],
    store: MemoryStore,
) -> List[str]:
    """Seal and post a :class:`RevocationNotice` to every peer's mailbox.

    Each peer gets a separately sealed copy — no peer can read another peer's
    copy, and the store cannot read any of them.

    Parameters
    ----------
    notice:
        A signed :class:`RevocationNotice` from :func:`create_token_revocation`
        or :func:`create_certificate_revocation`.
    peer_store_pub_keys:
        A sequence of raw 32-byte X25519 public keys — one per peer that
        should be notified.  Typically the store keys from the
        :class:`~proxion_messenger_core.federation.RelationshipCertificate` subjects.
    store:
        The coordination store.

    Returns
    -------
    list[str]
        The ``message_id`` strings assigned by the store for each delivery,
        in the same order as *peer_store_pub_keys*.  Useful for logging or
        retry logic.

    Raises
    ------
    ValueError
        If *notice* is unsigned (``signature`` is ``None``).
    """
    if not notice.signature:
        raise ValueError(
            "RevocationNotice must be signed before broadcasting — call notice.sign()"
        )
    message_ids: List[str] = []
    for pub_bytes in peer_store_pub_keys:
        mailbox = mailbox_id_for(pub_bytes)
        envelope = seal_json(notice.to_dict(), pub_bytes)
        msg_id = store.put(mailbox, envelope)
        message_ids.append(msg_id)
    return message_ids


def receive_revocations(
    my_store_priv: X25519PrivateKey,
    store: MemoryStore,
    revocation_list: RevocationList,
    *,
    seen_notice_ids: Optional[set] = None,
) -> List[RevocationNotice]:
    """Drain revocation notices from the mailbox and apply them locally.

    Decrypts all pending messages, keeps only those with
    ``@type == "RevocationNotice"``, verifies each signature, and calls
    :meth:`~proxion_messenger_core.revocation.RevocationList.revoke_until` for each
    valid notice.  Messages of other types are left in the mailbox.

    Parameters
    ----------
    my_store_priv:
        The recipient's X25519 private key — used to decrypt sealed envelopes.
    store:
        The coordination store.
    revocation_list:
        The local :class:`~proxion_messenger_core.revocation.RevocationList` to update.
    seen_notice_ids:
        Optional mutable set of already-processed ``notice_id`` values.  If
        provided, duplicate notices (e.g. from retried broadcasts) are silently
        skipped and their IDs are **not** added to the set again.  Callers
        SHOULD maintain this set across calls to ensure idempotency.

    Returns
    -------
    list[RevocationNotice]
        The notices that were successfully verified and applied.  Notices with
        invalid signatures or malformed payloads are discarded silently (logged
        at debug level in a production EI — not implemented here to avoid a
        logging dependency).

    Notes
    -----
    Uses :meth:`~proxion_messenger_core.store.MemoryStore.list_all` and
    :meth:`~proxion_messenger_core.store.MemoryStore.take_by_ids` so that handshake
    messages (invites, acceptances, certificates) coexisting in the same
    mailbox are left untouched.
    """
    pub_bytes = my_store_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    mailbox = mailbox_id_for(pub_bytes)
    stored = store.list_all(mailbox)

    applied: List[RevocationNotice] = []
    consumed_ids: set = set()

    for sm in stored:
        try:
            data = open_sealed_json(sm.envelope, my_store_priv)
        except Exception:
            continue   # malformed sealed envelope — not ours to consume

        if data.get("@type") != _MESSAGE_TYPE:
            continue   # belongs to another protocol layer

        try:
            notice = RevocationNotice.from_dict(data)
        except (KeyError, TypeError, ValueError):
            consumed_ids.add(sm.message_id)   # consume but discard malformed
            continue

        # Idempotency: skip if we've already applied this notice
        if seen_notice_ids is not None and notice.notice_id in seen_notice_ids:
            consumed_ids.add(sm.message_id)
            continue

        # Reject unsigned or invalid-signature notices
        if not notice.verify():
            consumed_ids.add(sm.message_id)   # consume and discard
            continue

        # Apply the revocation to the local list
        not_after_dt = datetime.fromtimestamp(notice.not_after, tz=timezone.utc)
        revocation_list.revoke_until(notice.revocation_id, not_after_dt)

        consumed_ids.add(sm.message_id)
        if seen_notice_ids is not None:
            seen_notice_ids.add(notice.notice_id)
        applied.append(notice)

    store.take_by_ids(mailbox, consumed_ids)
    return applied


# ---------------------------------------------------------------------------
# Convenience: revoke locally + broadcast in one call
# ---------------------------------------------------------------------------

def revoke_and_broadcast(
    subject: Token | RelationshipCertificate,
    issuer_priv: Ed25519PrivateKey,
    peer_store_pub_keys: Sequence[bytes],
    store: MemoryStore,
    revocation_list: RevocationList,
    reason: Optional[str] = None,
) -> RevocationNotice:
    """Revoke a token or certificate locally **and** broadcast to all peers.

    This is the primary high-level entry point for the revocation subsystem.
    It combines:

    1. Creating and signing a :class:`RevocationNotice`.
    2. Adding the revocation to the **local**
       :class:`~proxion_messenger_core.revocation.RevocationList` immediately (so the
       issuer's own resource server also starts denying the token without
       waiting for the next ``receive_revocations`` cycle).
    3. Sealing and posting the notice to every peer's mailbox.

    Parameters
    ----------
    subject:
        The :class:`~proxion_messenger_core.tokens.Token` or
        :class:`~proxion_messenger_core.federation.RelationshipCertificate` to revoke.
    issuer_priv:
        The issuer's Ed25519 private key.
    peer_store_pub_keys:
        Raw 32-byte X25519 public keys of all peers to notify.
    store:
        The coordination store.
    revocation_list:
        The issuer's local revocation list — updated immediately.
    reason:
        Optional free-text reason string.

    Returns
    -------
    RevocationNotice
        The signed notice that was broadcast (useful for logging or storing
        a local audit trail).

    Raises
    ------
    TypeError
        If *subject* is neither a :class:`~proxion_messenger_core.tokens.Token` nor a
        :class:`~proxion_messenger_core.federation.RelationshipCertificate`.
    """
    if isinstance(subject, Token):
        notice = create_token_revocation(subject, issuer_priv, reason)
        not_after_dt = subject.exp
    elif isinstance(subject, RelationshipCertificate):
        notice = create_certificate_revocation(subject, issuer_priv, reason)
        not_after_dt = datetime.fromtimestamp(subject.expires_at, tz=timezone.utc)
    else:
        raise TypeError(
            f"subject must be Token or RelationshipCertificate, got {type(subject).__name__}"
        )

    # Apply locally first — no delay, no dependency on the store.
    revocation_list.revoke_until(notice.revocation_id, not_after_dt)

    # Broadcast to peers.
    broadcast_revocation(notice, peer_store_pub_keys, store)

    return notice
