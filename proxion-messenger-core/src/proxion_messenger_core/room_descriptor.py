"""Room-descriptor signing/verification (PLAN_ROUND_71 B3).

A room's canonical descriptor (written to the host's pod, rooms/{id}/room.json) is
signed by the host's Ed25519 identity key so a gateway can trust it before
rehosting the room (B2). The cryptographic authority is the SIGNER did, recorded as
`px:signer`; the `owner` webid is descriptive. The gateway verifies the signature
and requires the signer to equal the rehost requester's auth-verified identity, so
a fabricated or tampered descriptor cannot rehost a room.

The canonical byte encoding mirrors the web client (roomdesc.js
descriptorSigningBytes) exactly — length-prefixed UTF-8 parts joined by '|', the
same scheme device_cert already uses across JS/Python — so a signature made in the
browser verifies here. `long_chat` and `updated` are deliberately NOT signed
(long_chat is filled server-side after signing; updated is not security relevant).
"""
from __future__ import annotations

import base64
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from .didkey import did_to_pub_key

_DOMAIN = b"proxion-room-descriptor-v1"
_US = "\x1f"   # unit separator, between a member's webid and role
_RS = "\x1e"   # record separator, between members


def _length_prefixed(parts: list[bytes]) -> bytes:
    chunks = [len(p).to_bytes(2, "big") + p for p in parts]
    return b"|".join(chunks)


def canonical_bytes(desc: dict) -> bytes:
    """The exact bytes signed over a descriptor. Must match roomdesc.js."""
    members = desc.get("members") or []
    norm = sorted(
        f"{m.get('webid', '')}{_US}{m.get('role', '')}"
        for m in members
        if isinstance(m, dict) and m.get("webid")
    )
    parts = [
        _DOMAIN,
        str(desc.get("room_id", "")).encode("utf-8"),
        str(desc.get("owner", "")).encode("utf-8"),
        str(desc.get("created", "")).encode("utf-8"),
        _RS.join(norm).encode("utf-8"),
    ]
    return _length_prefixed(parts)


def verify_descriptor(desc: dict) -> Optional[str]:
    """Return the signer did:key if the descriptor's signature is valid, else None.
    Never raises."""
    if not isinstance(desc, dict):
        return None
    signer = desc.get("px:signer") or ""
    sig_b64 = desc.get("px:sig") or ""
    if not signer or not sig_b64 or not signer.startswith("did:key:"):
        return None
    try:
        pub = Ed25519PublicKey.from_public_bytes(did_to_pub_key(signer))
        pub.verify(base64.b64decode(sig_b64), canonical_bytes(desc))
        return signer
    except (InvalidSignature, Exception):
        return None
