"""Round 1: scrypt+AES-256-GCM key envelope tests for AgentState persistence."""
import json
import pytest
from pathlib import Path

from proxion_messenger_core.persist import AgentState, PersistError
from proxion_messenger_core.key_envelope import (
    derive_wrap_key_scrypt,
    encrypt_key_bundle,
    decrypt_key_bundle,
)


# ---------------------------------------------------------------------------
# Unit tests: key_envelope helpers
# ---------------------------------------------------------------------------

def test_derive_wrap_key_returns_32_bytes():
    key = derive_wrap_key_scrypt(b"passphrase", b"0123456789abcdef")
    assert len(key) == 32
    assert isinstance(key, (bytes, bytearray))


def test_derive_wrap_key_deterministic():
    salt = b"testsalt12345678"
    k1 = derive_wrap_key_scrypt(b"pw", salt)
    k2 = derive_wrap_key_scrypt(b"pw", salt)
    assert k1 == k2


def test_encrypt_decrypt_roundtrip():
    # Derive two separate keys (encrypt zeroizes the key bytearray after use)
    key_enc = derive_wrap_key_scrypt(b"secret", b"saltsaltsaltsalt")
    key_dec = derive_wrap_key_scrypt(b"secret", b"saltsaltsaltsalt")
    bundle = {"foo": "bar", "num": 42}
    envelope = encrypt_key_bundle(bundle, key_enc)
    assert envelope["scheme"] == "scrypt-aes256gcm-v1"
    assert "nonce_b64" in envelope
    assert "ciphertext_b64" in envelope
    result = decrypt_key_bundle(envelope, key_dec)
    assert result == bundle


def test_wrong_wrap_key_raises_persist_error():
    key = derive_wrap_key_scrypt(b"correct", b"saltsaltsaltsalt")
    envelope = encrypt_key_bundle({"x": 1}, key)
    wrong_key = derive_wrap_key_scrypt(b"wrong", b"saltsaltsaltsalt")
    with pytest.raises(PersistError, match="invalid key envelope"):
        decrypt_key_bundle(envelope, wrong_key)


def test_tampered_ciphertext_raises_persist_error():
    key = derive_wrap_key_scrypt(b"pw", b"saltsaltsaltsalt")
    envelope = encrypt_key_bundle({"x": 1}, key)
    # Corrupt the ciphertext
    corrupted = dict(envelope)
    ct = list(corrupted["ciphertext_b64"])
    ct[0] = "A" if ct[0] != "A" else "B"
    corrupted["ciphertext_b64"] = "".join(ct)
    with pytest.raises(PersistError, match="invalid key envelope"):
        decrypt_key_bundle(corrupted, key)


# ---------------------------------------------------------------------------
# Integration tests: AgentState.save / load with new envelope format
# ---------------------------------------------------------------------------

def test_save_writes_scrypt_aesgcm_envelope(tmp_path):
    state = AgentState.generate()
    path = tmp_path / "agent.json"
    state.save(str(path), b"testpassword123")

    raw = json.loads(path.read_text())
    assert "@type" in raw
    assert "state_kdf" in raw, "new envelope format must be present"
    kdf = raw["state_kdf"]
    assert kdf["scheme"] == "scrypt-aes256gcm-v1"
    assert "salt_b64" in kdf
    assert kdf["n"] == 32768
    assert kdf["r"] == 8
    assert kdf["p"] == 1
    assert kdf["dklen"] == 32
    assert "nonce_b64" in kdf
    assert "ciphertext_b64" in kdf
    # Legacy PEM fields must NOT be present
    assert "identity_key_pem" not in raw
    assert "store_key_pem" not in raw


def test_load_new_format_round_trip(tmp_path):
    state = AgentState.generate()
    orig_pub = state.identity_pub_bytes
    path = tmp_path / "agent.json"
    state.save(str(path), b"roundtrippass123")
    loaded = AgentState.load(str(path), b"roundtrippass123")
    assert loaded.identity_pub_bytes == orig_pub


def test_wrong_passphrase_returns_generic_error(tmp_path):
    state = AgentState.generate()
    path = tmp_path / "agent.json"
    state.save(str(path), b"correctpassword1")
    with pytest.raises(PersistError, match="invalid key envelope"):
        AgentState.load(str(path), b"wrongpassword123")


def test_load_legacy_pem_and_resave_upgrades_format(tmp_path):
    """A state file with the old PEM format loads fine and saves in new format."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        BestAvailableEncryption, Encoding, PrivateFormat
    )

    passphrase = b"legacypassword123"
    id_key = Ed25519PrivateKey.generate()
    st_key = X25519PrivateKey.generate()
    legacy_data = {
        "@type": "ProxionAgentState",
        "version": 1,
        "identity_key_pem": id_key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(passphrase)
        ).decode("ascii"),
        "store_key_pem": st_key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(passphrase)
        ).decode("ascii"),
        "revocation_entries": {},
        "certificates": [],
        "pending_invites": [],
        "css_pod_url": None,
        "css_webid": None,
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy_data))

    state = AgentState.load(str(path), passphrase)
    assert state.identity_pub_bytes == id_key.public_key().public_bytes(
        Encoding.Raw, __import__("cryptography").hazmat.primitives.serialization.PublicFormat.Raw
    )

    # Re-save; should now write new envelope format
    new_path = tmp_path / "upgraded.json"
    state.save(str(new_path), passphrase)
    new_raw = json.loads(new_path.read_text())
    assert "state_kdf" in new_raw
    assert "identity_key_pem" not in new_raw
