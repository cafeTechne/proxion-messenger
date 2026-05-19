"""Tests for proxion_messenger_core.sealed — SealedEnvelope and mailbox addressing."""

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.errors import CipherError
from proxion_messenger_core.sealed import (
    SealedEnvelope,
    mailbox_id_for,
    open_sealed,
    open_sealed_json,
    seal,
    seal_json,
)


@pytest.fixture
def recipient_priv():
    return X25519PrivateKey.generate()


@pytest.fixture
def recipient_pub_bytes(recipient_priv):
    return recipient_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


# ---------------------------------------------------------------------------
# seal / open_sealed round-trip
# ---------------------------------------------------------------------------

def test_round_trip_bytes(recipient_priv, recipient_pub_bytes):
    plaintext = b"hello proxion"
    env = seal(plaintext, recipient_pub_bytes)
    assert open_sealed(env, recipient_priv) == plaintext


def test_round_trip_json(recipient_priv, recipient_pub_bytes):
    data = {"type": "FederationInvite", "id": "abc", "caps": [1, 2, 3]}
    env = seal_json(data, recipient_pub_bytes)
    assert open_sealed_json(env, recipient_priv) == data


def test_round_trip_empty_bytes(recipient_priv, recipient_pub_bytes):
    env = seal(b"", recipient_pub_bytes)
    assert open_sealed(env, recipient_priv) == b""


def test_round_trip_large_payload(recipient_priv, recipient_pub_bytes):
    big = b"x" * 100_000
    env = seal(big, recipient_pub_bytes)
    assert open_sealed(env, recipient_priv) == big


# ---------------------------------------------------------------------------
# Envelope structure
# ---------------------------------------------------------------------------

def test_envelope_has_ephemeral_pub(recipient_pub_bytes):
    env = seal(b"test", recipient_pub_bytes)
    assert len(env.ephemeral_pub) == 32


def test_envelope_has_nonce(recipient_pub_bytes):
    env = seal(b"test", recipient_pub_bytes)
    assert len(env.nonce) == 12


def test_fresh_ciphertext_per_call(recipient_pub_bytes):
    """Two seals of the same plaintext must produce different ciphertexts (fresh ephemeral key)."""
    e1 = seal(b"same", recipient_pub_bytes)
    e2 = seal(b"same", recipient_pub_bytes)
    assert e1.ciphertext != e2.ciphertext
    assert e1.ephemeral_pub != e2.ephemeral_pub


# ---------------------------------------------------------------------------
# SealedEnvelope serialisation
# ---------------------------------------------------------------------------

def test_envelope_to_from_dict_roundtrip(recipient_pub_bytes):
    env = seal(b"data", recipient_pub_bytes)
    restored = SealedEnvelope.from_dict(env.to_dict())
    assert restored == env


def test_envelope_type_field(recipient_pub_bytes):
    d = seal(b"x", recipient_pub_bytes).to_dict()
    assert d["@type"] == "SealedEnvelope"


def test_envelope_from_dict_missing_field():
    with pytest.raises(CipherError, match="malformed"):
        SealedEnvelope.from_dict({"ephemeral_pub": "AA==", "nonce": "BB=="})  # missing ciphertext


def test_envelope_byte_size(recipient_pub_bytes):
    env = seal(b"hello", recipient_pub_bytes)
    assert env.byte_size == len(env.ephemeral_pub) + len(env.nonce) + len(env.ciphertext)


# ---------------------------------------------------------------------------
# Authentication — wrong key and tamper detection
# ---------------------------------------------------------------------------

def test_wrong_recipient_key_rejected(recipient_pub_bytes):
    env = seal(b"secret", recipient_pub_bytes)
    wrong_priv = X25519PrivateKey.generate()
    with pytest.raises(CipherError, match="wrong key or tampered"):
        open_sealed(env, wrong_priv)


def test_tampered_ciphertext_rejected(recipient_priv, recipient_pub_bytes):
    env = seal(b"secret", recipient_pub_bytes)
    flipped = bytes([env.ciphertext[0] ^ 0xFF]) + env.ciphertext[1:]
    bad = SealedEnvelope(env.ephemeral_pub, env.nonce, flipped)
    with pytest.raises(CipherError, match="wrong key or tampered"):
        open_sealed(bad, recipient_priv)


def test_invalid_recipient_key_size_rejected():
    with pytest.raises(CipherError):
        seal(b"x", b"\x00" * 10)   # too short


# ---------------------------------------------------------------------------
# mailbox_id_for
# ---------------------------------------------------------------------------

def test_mailbox_id_deterministic(recipient_pub_bytes):
    assert mailbox_id_for(recipient_pub_bytes) == mailbox_id_for(recipient_pub_bytes)


def test_mailbox_id_different_keys():
    k1 = X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    k2 = X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    assert mailbox_id_for(k1) != mailbox_id_for(k2)


def test_mailbox_id_is_hex_string(recipient_pub_bytes):
    mid = mailbox_id_for(recipient_pub_bytes)
    assert isinstance(mid, str)
    int(mid, 16)   # must be valid hex
