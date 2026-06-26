"""Tests for proxion_messenger_core.didkey."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.didkey import pub_key_to_did, did_to_pub_key, agent_did, _base58btc_encode, _base58btc_decode
from proxion_messenger_core.persist import AgentState


def test_pub_key_to_did_returns_did_key_prefix():
    """did:key encoding starts with the correct prefix."""
    # Generate a test Ed25519 key
    private_key = Ed25519PrivateKey.generate()
    pub_key = private_key.public_key()
    
    from cryptography.hazmat.primitives import serialization
    pub_bytes = pub_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    
    did = pub_key_to_did(pub_bytes)
    assert did.startswith("did:key:z6Mk")


def test_did_roundtrip():
    """did_to_pub_key(pub_key_to_did(key_bytes)) == key_bytes."""
    # Generate a test Ed25519 key
    private_key = Ed25519PrivateKey.generate()
    pub_key = private_key.public_key()
    
    from cryptography.hazmat.primitives import serialization
    original_bytes = pub_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    
    # Roundtrip
    did = pub_key_to_did(original_bytes)
    recovered_bytes = did_to_pub_key(did)
    
    assert recovered_bytes == original_bytes


def test_did_invalid_prefix_raises():
    """did_to_pub_key rejects non-did:key DIDs."""
    with pytest.raises(ValueError, match="Invalid did:key format"):
        did_to_pub_key("did:web:example.com")


def test_agent_did_stable():
    """Same AgentState produces same DID on repeated calls."""
    agent = AgentState.generate()
    
    did1 = agent_did(agent)
    did2 = agent_did(agent)
    
    assert did1 == did2


def test_did_length_is_reasonable():
    """did:key output is a reasonable length (50-60 chars)."""
    private_key = Ed25519PrivateKey.generate()
    pub_key = private_key.public_key()

    from cryptography.hazmat.primitives import serialization
    pub_bytes = pub_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    did = pub_key_to_did(pub_bytes)

    # did:key:z6Mk prefix is 13 chars, base58btc of (0xed01 + 32 bytes) should be ~43-44 chars
    # Total should be around 56-57 chars
    assert 50 <= len(did) <= 60


# ---------------------------------------------------------------------------
# _base58btc_encode edge cases (lines 27, 41)
# ---------------------------------------------------------------------------

def test_base58btc_encode_empty_returns_empty():
    assert _base58btc_encode(b"") == ""


def test_base58btc_encode_leading_zero_bytes():
    # b"\x00\x01" → leading zero becomes leading "1" in base58btc
    result = _base58btc_encode(b"\x00\x01")
    assert result.startswith("1")


def test_base58btc_encode_all_zero_bytes():
    result = _base58btc_encode(b"\x00\x00\x00")
    assert result == "111"


# ---------------------------------------------------------------------------
# _base58btc_decode edge cases (lines 54, 60, 68, 73)
# ---------------------------------------------------------------------------

def test_base58btc_decode_empty_returns_empty():
    assert _base58btc_decode("") == b""


def test_base58btc_decode_leading_ones():
    # "1" characters map to leading zero bytes
    result = _base58btc_decode("111")
    assert result == b"\x00\x00\x00"


def test_base58btc_decode_invalid_char_raises():
    with pytest.raises(ValueError, match="Invalid base58btc character"):
        _base58btc_decode("0OIl")  # '0' and 'O' and 'I' and 'l' are excluded from base58btc


def test_base58btc_encode_decode_roundtrip_with_leading_zeros():
    original = b"\x00\x00\xde\xad\xbe\xef"
    assert _base58btc_decode(_base58btc_encode(original)) == original


# ---------------------------------------------------------------------------
# pub_key_to_did error (line 98)
# ---------------------------------------------------------------------------

def test_pub_key_to_did_wrong_length_raises():
    with pytest.raises(ValueError, match="Expected 32-byte"):
        pub_key_to_did(b"\x01" * 16)


# ---------------------------------------------------------------------------
# did_to_pub_key error paths (lines 136-137, 141, 149)
# ---------------------------------------------------------------------------

def test_did_to_pub_key_invalid_base58_raises():
    # inject an invalid base58btc char ('0') in the encoded portion
    with pytest.raises(ValueError, match="Invalid base58btc"):
        did_to_pub_key("did:key:z000invalid0")


def test_did_to_pub_key_wrong_multicodec_raises():
    # Encode 34 bytes with the wrong multicodec prefix (0x1200 instead of 0xed01)
    wrong_prefix = b"\x12\x00" + b"\x42" * 32
    encoded = _base58btc_encode(wrong_prefix)
    with pytest.raises(ValueError, match="Not an Ed25519 key"):
        did_to_pub_key(f"did:key:z{encoded}")


def test_did_to_pub_key_wrong_key_length_raises():
    # Encode a valid-looking multicodec prefix but with only 16 bytes of key data
    too_short = b"\xed\x01" + b"\x42" * 16
    encoded = _base58btc_encode(too_short)
    with pytest.raises(ValueError, match="Not an Ed25519 key"):
        did_to_pub_key(f"did:key:z{encoded}")
