"""Tests for proxion_messenger_core.crypto — AES-256-GCM Cipher."""

import os
import pytest
from proxion_messenger_core.crypto import Cipher
from proxion_messenger_core.errors import CipherError


@pytest.fixture
def key():
    return os.urandom(32)


@pytest.fixture
def cipher(key):
    return Cipher(key)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_wrong_key_size_raises():
    with pytest.raises(CipherError, match="32 bytes"):
        Cipher(b"tooshort")


def test_empty_key_raises():
    with pytest.raises(CipherError):
        Cipher(b"")


def test_31_byte_key_raises():
    with pytest.raises(CipherError):
        Cipher(os.urandom(31))


def test_33_byte_key_raises():
    with pytest.raises(CipherError):
        Cipher(os.urandom(33))


# ---------------------------------------------------------------------------
# Round-trip correctness
# ---------------------------------------------------------------------------

def test_round_trip_dict(cipher):
    data = {"user": "alice", "perms": ["read", "write"], "level": 2}
    assert cipher.decrypt(cipher.encrypt(data)) == data


def test_round_trip_string(cipher):
    data = "hello, proxion"
    assert cipher.decrypt(cipher.encrypt(data)) == data


def test_round_trip_list(cipher):
    data = [1, "two", {"three": 3}, None]
    assert cipher.decrypt(cipher.encrypt(data)) == data


def test_round_trip_empty_dict(cipher):
    assert cipher.decrypt(cipher.encrypt({})) == {}


def test_round_trip_nested(cipher):
    data = {"a": {"b": {"c": [1, 2, 3]}}}
    assert cipher.decrypt(cipher.encrypt(data)) == data


# ---------------------------------------------------------------------------
# Envelope structure
# ---------------------------------------------------------------------------

def test_envelope_alg_field(cipher):
    env = cipher.encrypt("x")
    assert env["alg"] == "AES-256-GCM"


def test_envelope_type_field(cipher):
    env = cipher.encrypt("x")
    assert env["@type"] == "EncryptedResource"


def test_envelope_has_nonce_and_ciphertext(cipher):
    env = cipher.encrypt("x")
    assert "nonce" in env
    assert "ciphertext" in env
    assert isinstance(env["nonce"], str)
    assert isinstance(env["ciphertext"], str)


# ---------------------------------------------------------------------------
# Fresh nonce per encrypt call
# ---------------------------------------------------------------------------

def test_fresh_nonce_per_call(cipher):
    """Two encryptions of the same data must produce different nonces."""
    e1 = cipher.encrypt("same")
    e2 = cipher.encrypt("same")
    assert e1["nonce"] != e2["nonce"]
    assert e1["ciphertext"] != e2["ciphertext"]


# ---------------------------------------------------------------------------
# Authentication / tamper detection
# ---------------------------------------------------------------------------

def test_wrong_key_rejected():
    import base64
    k1, k2 = os.urandom(32), os.urandom(32)
    env = Cipher(k1).encrypt({"secret": "value"})
    with pytest.raises(CipherError, match="wrong key or tampered"):
        Cipher(k2).decrypt(env)


def test_tampered_ciphertext_rejected(cipher):
    import base64
    env = cipher.encrypt("sensitive")
    raw = base64.urlsafe_b64decode(env["ciphertext"] + "==")
    flipped = bytes([raw[0] ^ 0xFF]) + raw[1:]
    bad_env = dict(env, ciphertext=base64.urlsafe_b64encode(flipped).decode())
    with pytest.raises(CipherError, match="wrong key or tampered"):
        cipher.decrypt(bad_env)


def test_tampered_nonce_rejected(cipher):
    import base64
    env = cipher.encrypt("sensitive")
    raw = base64.urlsafe_b64decode(env["nonce"] + "==")
    flipped = bytes([raw[0] ^ 0xFF]) + raw[1:]
    bad_env = dict(env, nonce=base64.urlsafe_b64encode(flipped).decode())
    with pytest.raises(CipherError):
        cipher.decrypt(bad_env)


# ---------------------------------------------------------------------------
# Unsupported / malformed envelopes
# ---------------------------------------------------------------------------

def test_unsupported_alg_rejected(cipher):
    env = cipher.encrypt("x")
    bad = dict(env, alg="RSA-OAEP")
    with pytest.raises(CipherError, match="unsupported alg"):
        cipher.decrypt(bad)


def test_missing_nonce_rejected(cipher):
    env = cipher.encrypt("x")
    env.pop("nonce")
    with pytest.raises(CipherError):
        cipher.decrypt(env)


def test_missing_ciphertext_rejected(cipher):
    env = cipher.encrypt("x")
    env.pop("ciphertext")
    with pytest.raises(CipherError):
        cipher.decrypt(env)


def test_wrong_length_nonce_rejected(cipher):
    import base64
    env = cipher.encrypt("x")
    # Replace nonce with 8 bytes instead of 12 — hits the len(nonce) != _NONCE_SIZE check
    short_nonce = base64.urlsafe_b64encode(b"\x00" * 8).decode()
    bad_env = dict(env, nonce=short_nonce)
    with pytest.raises(CipherError, match="nonce must be"):
        cipher.decrypt(bad_env)
