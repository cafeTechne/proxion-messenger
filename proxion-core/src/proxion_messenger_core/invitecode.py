"""Invite code encoding and decoding with compression."""
from __future__ import annotations

import base64
import json
import zlib
from typing import Any

INVITE_PREFIX = "prx1_"

def encode_invite(invite: dict[str, Any]) -> str:
    """Encode an invite dictionary into a compressed, base64-encoded string.
    
    Uses zlib level 9 compression and URL-safe base64 encoding.
    """
    json_data = json.dumps(invite).encode("utf-8")
    compressed = zlib.compress(json_data, level=9)
    encoded = base64.urlsafe_b64encode(compressed).decode("utf-8").rstrip("=")
    return f"{INVITE_PREFIX}{encoded}"

def decode_invite(code: str) -> dict[str, Any]:
    """Decode an invite code back into a dictionary.
    
    Validates the 'prx1_' prefix before decompressing.
    """
    if not code.startswith(INVITE_PREFIX):
        raise ValueError("invalid invite code format")
    
    raw_encoded = code[len(INVITE_PREFIX):]
    # Add padding back if necessary
    padding = "=" * (4 - len(raw_encoded) % 4) if len(raw_encoded) % 4 != 0 else ""
    try:
        compressed = base64.urlsafe_b64decode(raw_encoded + padding)
        json_data = zlib.decompress(compressed).decode("utf-8")
        return json.loads(json_data)
    except Exception as e:
        raise ValueError(f"failed to decode invite code: {e}")
