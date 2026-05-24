"""Tests: seal_relay_payload / unseal_relay_payload round-trip."""
from __future__ import annotations
import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
import base64

from proxion_messenger_core.sealed_relay import seal_relay_payload, unseal_relay_payload


def _keypair():
    priv = X25519PrivateKey.generate()
    priv_bytes = priv.private_bytes_raw()
    pub_b64 = base64.urlsafe_b64encode(priv.public_key().public_bytes_raw()).rstrip(b"=").decode()
    return priv_bytes, pub_b64


def test_seal_unseal_roundtrip():
    """seal then unseal with matching keys recovers original payload."""
    priv_bytes, pub_b64 = _keypair()
    payload = {"from_webid": "did:key:zA", "content": "hello", "message_id": "m1"}
    sealed = seal_relay_payload(payload, pub_b64)
    recovered = unseal_relay_payload(sealed, priv_bytes)
    assert recovered == payload


def test_seal_wrong_key_fails():
    """unseal with wrong private key raises ValueError."""
    _, pub_b64 = _keypair()
    wrong_priv_bytes, _ = _keypair()
    payload = {"from_webid": "did:key:zA", "content": "secret"}
    sealed = seal_relay_payload(payload, pub_b64)
    with pytest.raises(ValueError):
        unseal_relay_payload(sealed, wrong_priv_bytes)


def test_seal_truncated_payload_fails():
    """unseal raises ValueError for truncated sealed payload."""
    priv_bytes, _ = _keypair()
    with pytest.raises(ValueError):
        unseal_relay_payload("AAAA", priv_bytes)


def test_sealed_payload_is_opaque_string():
    """sealed payload contains no plaintext from the original dict."""
    _, pub_b64 = _keypair()
    payload = {"from_webid": "alice@secret.com", "content": "classified"}
    sealed = seal_relay_payload(payload, pub_b64)
    # sealed should be base64url and NOT contain plaintext
    assert "alice@secret.com" not in sealed
    assert "classified" not in sealed


def test_seal_different_nonces_each_call():
    """Two seals of the same payload produce different ciphertexts (random nonce)."""
    _, pub_b64 = _keypair()
    payload = {"from_webid": "did:key:zA", "content": "same"}
    sealed1 = seal_relay_payload(payload, pub_b64)
    sealed2 = seal_relay_payload(payload, pub_b64)
    assert sealed1 != sealed2
