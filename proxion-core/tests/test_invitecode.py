"""Tests for proxion_messenger_core.invitecode."""
from __future__ import annotations

import pytest
from proxion_messenger_core.invitecode import encode_invite, decode_invite

def test_encode_decode_roundtrip():
    invite = {
        "invite_id": "test-123",
        "inviter": "alice",
        "capabilities": [{"can": "read", "with": "stash://test"}]
    }
    code = encode_invite(invite)
    assert code.startswith("prx1_")
    
    decoded = decode_invite(code)
    assert decoded["invite_id"] == "test-123"
    assert decoded["inviter"] == "alice"
    assert decoded["capabilities"][0]["can"] == "read"

def test_decode_invalid_prefix():
    with pytest.raises(ValueError, match="invalid invite code format"):
        decode_invite("invalid_prefix_abc123")

def test_decode_malformed_data():
    with pytest.raises(ValueError, match="failed to decode"):
        decode_invite("prx1_notbase64")

def test_encode_invite_is_valid_base64url():
    import re
    invite = {"simple": "data"}
    code = encode_invite(invite)
    # Strip prefix
    raw_encoded = code[len("prx1_"):]
    # Should not contain +, /, or =
    assert re.match(r"^[A-Za-z0-9_-]+$", raw_encoded)

def test_encode_invite_large_dict_under_512_chars():
    # A dict with 20 string fields each 50 chars long
    invite = {f"key{i}": "A" * 50 for i in range(20)}
    code = encode_invite(invite)
    assert len(code) < 512
