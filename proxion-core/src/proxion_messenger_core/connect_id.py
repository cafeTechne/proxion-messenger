"""Self-contained Connect ID codec for Proxion.

Encodes a DID + gateway URL into a compact user-shareable string so that
non-technical users can exchange one string instead of long technical addresses.

Wire format: ``proxion:<urlsafe_b64(zlib(json({did, url})))>#<4-hex-checksum>``

No network calls. No external coordinator. The full address is embedded in the ID.
"""
from __future__ import annotations

import base64
import hashlib
import json
import zlib

CONNECT_ID_PREFIX = "proxion:"
_CHECKSUM_LEN = 4


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _checksum(payload_b64: str) -> str:
    return hashlib.sha256(payload_b64.encode()).hexdigest()[:_CHECKSUM_LEN]


def encode_connect_id(did: str, gateway_url: str) -> str:
    """Encode a DID and gateway URL into a Connect ID string."""
    payload = json.dumps({"did": did, "url": gateway_url}, separators=(",", ":"))
    b64 = _b64(zlib.compress(payload.encode(), level=9))
    return f"{CONNECT_ID_PREFIX}{b64}#{_checksum(b64)}"


def decode_connect_id(connect_id: str) -> dict:
    """Decode a Connect ID, returning ``{"did": ..., "url": ...}``.

    Raises
    ------
    ValueError
        If the format is invalid, the checksum is wrong, or the payload is corrupt.
    """
    if not connect_id.startswith(CONNECT_ID_PREFIX):
        raise ValueError(f"invalid connect_id prefix: expected '{CONNECT_ID_PREFIX}'")
    rest = connect_id[len(CONNECT_ID_PREFIX):]
    if "#" not in rest:
        raise ValueError("invalid connect_id: missing checksum separator")
    b64_part, checksum_part = rest.rsplit("#", 1)
    expected = _checksum(b64_part)
    if checksum_part != expected:
        raise ValueError(
            f"invalid connect_id: checksum mismatch (got {checksum_part!r}, expected {expected!r})"
        )
    try:
        raw = zlib.decompress(_unb64(b64_part))
        return json.loads(raw.decode())
    except Exception as exc:
        raise ValueError(f"invalid connect_id: payload decode failed: {exc}") from exc


def is_valid_connect_id(connect_id: str) -> bool:
    """Return True if the Connect ID has valid format and checksum."""
    try:
        decode_connect_id(connect_id)
        return True
    except ValueError:
        return False
