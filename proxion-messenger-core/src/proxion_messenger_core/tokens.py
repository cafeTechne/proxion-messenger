"""Capability token issuance and integrity checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
import hashlib
import json
import secrets
import base64
from typing import FrozenSet, Iterable, List, Optional, Tuple

from .context import Caveat
from .errors import TokenError


@dataclass(frozen=True)
class Token:
    token_id: str
    permissions: FrozenSet[Tuple[str, str]]
    exp: datetime
    aud: str
    caveats: Tuple[Caveat, ...]
    holder_key_fingerprint: str
    alg: str
    signature: str
    parent_token_id: Optional[str] = None

    def payload(self) -> dict:
        d = {
            "token_id": self.token_id,
            "permissions": sorted([list(p) for p in self.permissions]),
            "exp": _coerce_datetime(self.exp).isoformat(),
            "aud": self.aud,
            "caveats": [c.id for c in self.caveats],
            "holder_key_fingerprint": self.holder_key_fingerprint,
        }
        if self.parent_token_id is not None:
            d["parent_token_id"] = self.parent_token_id
        return d


def _coerce_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def token_canonical_bytes(token: "Token") -> bytes:
    return _canonical_json(token.payload())


def _sign(payload: dict, signing_key: bytes) -> str:
    digest = hmac.new(signing_key, _canonical_json(payload), hashlib.sha256).digest()
    return _b64url(digest)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def issue_token(
    permissions: Iterable[Tuple[str, str]],
    exp: datetime,
    aud: str,
    caveats: Iterable[Caveat],
    holder_key_fingerprint: str,
    signing_key: bytes,
    now: Optional[datetime] = None,
    token_id: Optional[str] = None,
    parent_token_id: Optional[str] = None,
) -> Token:
    now_dt = _coerce_datetime(now or datetime.now(timezone.utc))
    exp_dt = _coerce_datetime(exp)
    if exp_dt <= now_dt:
        raise TokenError("expiration must be in the future")
    perms = frozenset(permissions)
    if not perms:
        raise TokenError("permissions must be non-empty")
    caveat_tuple = tuple(caveats)
    tok_id = token_id or secrets.token_urlsafe(24)
    payload = {
        "token_id": tok_id,
        "permissions": sorted([list(p) for p in perms]),
        "exp": exp_dt.isoformat(),
        "aud": aud,
        "caveats": [c.id for c in caveat_tuple],
        "holder_key_fingerprint": holder_key_fingerprint,
    }
    if parent_token_id is not None:
        payload["parent_token_id"] = parent_token_id
    signature = _sign(payload, signing_key)
    return Token(
        token_id=tok_id,
        permissions=perms,
        exp=exp_dt,
        aud=aud,
        caveats=caveat_tuple,
        holder_key_fingerprint=holder_key_fingerprint,
        alg="HMAC-SHA256",
        signature=signature,
        parent_token_id=parent_token_id,
    )


def verify_integrity(token: Token, signing_key: bytes) -> bool:
    if token.alg != "HMAC-SHA256":
        raise TokenError("unsupported alg")
    expected = _sign(token.payload(), signing_key)
    if not hmac.compare_digest(expected, token.signature):
        raise TokenError("signature mismatch")
    return True
