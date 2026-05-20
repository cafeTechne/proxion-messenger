"""Tests for sealed sender protocol (Round 18)."""
import os
import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.sealed_sender import seal, unseal, make_sender_cert, verify_sender_cert


def _x25519_pair():
    priv = X25519PrivateKey.generate()
    priv_bytes = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub_bytes = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return priv_bytes, pub_bytes


def test_seal_unseal_roundtrip():
    """seal / unseal roundtrip returns original sender cert and message."""
    recipient_priv, recipient_pub = _x25519_pair()
    sender_key = Ed25519PrivateKey.generate()

    cert = make_sender_cert("alice@example.org", sender_key)
    message = {"type": "message", "content": "hello world", "thread_id": "dm-123"}

    sealed = seal(cert, message, recipient_pub)
    recovered_cert, recovered_message = unseal(sealed, recipient_priv)

    assert recovered_cert["webid"] == "alice@example.org"
    assert recovered_message["content"] == "hello world"
    assert recovered_message["thread_id"] == "dm-123"


def test_sealed_message_gateway_sees_only_recipient():
    """Sealed bytes do not contain the sender WebID in plaintext."""
    recipient_priv, recipient_pub = _x25519_pair()
    sender_key = Ed25519PrivateKey.generate()
    cert = make_sender_cert("alice@example.org", sender_key)
    message = {"content": "secret"}

    sealed = seal(cert, message, recipient_pub)
    # Gateway only sees raw bytes — sender WebID must not appear
    assert b"alice@example.org" not in sealed


def test_tampered_sealed_message_rejected():
    """Flipping a byte in the ciphertext raises an exception on unseal."""
    from cryptography.exceptions import InvalidTag
    recipient_priv, recipient_pub = _x25519_pair()
    sender_key = Ed25519PrivateKey.generate()
    cert = make_sender_cert("alice@example.org", sender_key)
    message = {"content": "tamper me"}

    sealed = seal(cert, message, recipient_pub)
    tampered = bytearray(sealed)
    tampered[-1] ^= 0xFF  # flip last byte of ciphertext
    with pytest.raises((InvalidTag, Exception)):
        unseal(bytes(tampered), recipient_priv)


def test_sender_cert_verify_passes():
    key = Ed25519PrivateKey.generate()
    cert = make_sender_cert("alice@example.org", key)
    assert verify_sender_cert(cert) is True


def test_sender_cert_verify_fails_on_tamper():
    key = Ed25519PrivateKey.generate()
    cert = make_sender_cert("alice@example.org", key)
    cert["webid"] = "eve@malicious.org"
    assert verify_sender_cert(cert) is False
