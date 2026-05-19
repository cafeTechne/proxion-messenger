"""Unit tests for cross-gateway relay signing and verification."""
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.relay import (
    sign_relay_message,
    verify_relay_message,
    parse_proxion_address,
    format_proxion_address,
    _validate_relay_target,
)
from proxion_messenger_core.didkey import pub_key_to_did


@pytest.fixture
def key_pair():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    return priv, pub


def test_sign_and_verify(key_pair):
    from datetime import datetime, timezone, timedelta
    priv, _ = key_pair
    from_did = pub_key_to_did(priv.public_key().public_bytes_raw())
    to_did = "did:key:z6Mktest"
    msg_id = "msg-001"
    content = "hello world"
    ts = datetime.now(timezone.utc).isoformat()

    sig = sign_relay_message(priv, from_did, to_did, msg_id, content, ts)
    assert verify_relay_message(from_did, to_did, msg_id, content, ts, sig)


def test_verify_fails_tampered_content(key_pair):
    priv, _ = key_pair
    from_did = pub_key_to_did(priv.public_key().public_bytes_raw())
    to_did = "did:key:z6Mktest"
    msg_id = "msg-002"
    ts = "2024-01-01T00:00:00Z"

    sig = sign_relay_message(priv, from_did, to_did, msg_id, "original", ts)
    assert not verify_relay_message(from_did, to_did, msg_id, "tampered", ts, sig)


def test_verify_fails_wrong_sender(key_pair):
    priv, _ = key_pair
    other_priv = Ed25519PrivateKey.generate()
    other_did = pub_key_to_did(other_priv.public_key().public_bytes_raw())
    from_did = pub_key_to_did(priv.public_key().public_bytes_raw())
    to_did = "did:key:z6Mktest"
    msg_id = "msg-003"
    content = "hello"
    ts = "2024-01-01T00:00:00Z"

    sig = sign_relay_message(priv, from_did, to_did, msg_id, content, ts)
    # Verification against the wrong sender DID must fail
    assert not verify_relay_message(other_did, to_did, msg_id, content, ts, sig)


def test_parse_proxion_address():
    addr = "did:key:z6Mktest@https://gateway.example.com"
    did, gw = parse_proxion_address(addr)
    assert did == "did:key:z6Mktest"
    assert gw == "https://gateway.example.com"


def test_parse_proxion_address_no_gateway():
    did, gw = parse_proxion_address("did:key:z6Mktest")
    assert did == "did:key:z6Mktest"
    assert gw is None


def test_format_proxion_address():
    addr = format_proxion_address("did:key:z6Mktest", "https://gateway.example.com")
    assert addr == "did:key:z6Mktest@https://gateway.example.com"


def test_validate_relay_target_allows_public_ip():
    # Public IP literal — resolves to itself without a DNS lookup.
    assert _validate_relay_target("https://1.1.1.1/relay")


def test_validate_relay_target_rejects_localhost_without_flag():
    assert not _validate_relay_target("http://localhost:8080/relay")


def test_validate_relay_target_allows_localhost_with_flag(monkeypatch):
    import os
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    assert _validate_relay_target("http://localhost:8080/relay")
    assert _validate_relay_target("http://127.0.0.1:8081/relay")


def test_validate_relay_target_rejects_credential_bypass():
    # http://user@127.0.0.1/ — urlparse.hostname strips userinfo → loopback detected
    assert not _validate_relay_target("http://user@127.0.0.1/relay")


def test_validate_relay_target_rejects_private_ranges():
    assert not _validate_relay_target("http://10.0.0.1/relay")
    assert not _validate_relay_target("http://192.168.1.1/relay")
    assert not _validate_relay_target("https://169.254.169.254/latest/meta-data/")
