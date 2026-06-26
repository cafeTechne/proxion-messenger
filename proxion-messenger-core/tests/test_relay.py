"""Tests for relay.py — signing, verification, address parsing."""
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.relay import (
    sign_relay_message,
    verify_relay_message,
    parse_proxion_address,
    format_proxion_address,
)
from proxion_messenger_core.didkey import pub_key_to_did


@pytest.fixture
def keypair():
    key = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization
    pub_bytes = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    did = pub_key_to_did(pub_bytes)
    return key, did


def test_sign_returns_nonempty_string(keypair):
    key, did = keypair
    sig = sign_relay_message(key, did, "did:key:target", "msg-1", "hello", "2026-04-16T10:00:00+00:00")
    assert isinstance(sig, str)
    assert len(sig) > 0


def test_verify_valid_signature(keypair):
    from datetime import datetime, timezone
    key, did = keypair
    ts = datetime.now(timezone.utc).isoformat()
    sig = sign_relay_message(key, did, "did:key:target", "msg-2", "hi", ts)
    assert verify_relay_message(did, "did:key:target", "msg-2", "hi", ts, sig)


def test_verify_wrong_key_fails():
    key1 = Ed25519PrivateKey.generate()
    key2 = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization
    pub2 = key2.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    did2 = pub_key_to_did(pub2)

    # Sign with key1 but claim it's from key2's DID
    from cryptography.hazmat.primitives import serialization as _s
    pub1 = key1.public_key().public_bytes(_s.Encoding.Raw, _s.PublicFormat.Raw)
    did1 = pub_key_to_did(pub1)
    sig = sign_relay_message(key1, did1, "did:key:bob", "msg-3", "spoofed", "2026-04-16T10:00:00+00:00")
    # Verify claiming it came from did2 — should fail
    assert not verify_relay_message(did2, "did:key:bob", "msg-3", "spoofed", "2026-04-16T10:00:00+00:00", sig)


def test_verify_tampered_content_fails(keypair):
    key, did = keypair
    sig = sign_relay_message(key, did, "did:key:target", "msg-4", "original", "2026-04-16T10:00:00+00:00")
    assert not verify_relay_message(did, "did:key:target", "msg-4", "TAMPERED", "2026-04-16T10:00:00+00:00", sig)


def test_verify_bad_signature_string_returns_false(keypair):
    _, did = keypair
    assert not verify_relay_message(did, "did:key:target", "msg-5", "hi", "2026-04-16T10:00:00+00:00", "notasignature")


def test_verify_invalid_did_returns_false():
    assert not verify_relay_message(
        "did:key:invalid!!!", "did:key:target", "m", "c", "t", "sig"
    )


# ── Address parsing ──────────────────────────────────────────────────────────

def test_parse_full_proxion_address():
    did, gw = parse_proxion_address("did:key:z6MkAlice@https://chat.example.com")
    assert did == "did:key:z6MkAlice"
    assert gw == "https://chat.example.com"


def test_parse_address_no_gateway():
    did, gw = parse_proxion_address("did:key:z6MkAlice")
    assert did == "did:key:z6MkAlice"
    assert gw is None


def test_parse_address_http_gateway():
    did, gw = parse_proxion_address("did:key:z6Mk@http://192.168.1.10:7474")
    assert did == "did:key:z6Mk"
    assert gw == "http://192.168.1.10:7474"


def test_parse_address_strips_whitespace():
    did, gw = parse_proxion_address("  did:key:z6Mk@https://example.com  ")
    assert did == "did:key:z6Mk"
    assert gw == "https://example.com"


def test_format_proxion_address():
    addr = format_proxion_address("did:key:z6Mk", "https://chat.example.com")
    assert addr == "did:key:z6Mk@https://chat.example.com"
