"""R9: Signed config startup verification tests."""
import json
import os
import pytest

from proxion_messenger_core.config_verify import (
    load_verified_config,
    check_signed_config_startup,
    ConfigVerificationError,
)


def _generate_keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
    priv_bytes = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_bytes = pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv_bytes, pub_bytes


def _sign_config(priv_bytes: bytes, config_bytes: bytes) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    priv = Ed25519PrivateKey.from_private_bytes(priv_bytes)
    sig = priv.sign(config_bytes)
    return sig.hex()


def test_startup_succeeds_with_valid_signed_config(tmp_path, monkeypatch):
    priv_bytes, pub_bytes = _generate_keypair()
    config = {"key": "value"}
    config_bytes = json.dumps(config).encode()
    sig_hex = _sign_config(priv_bytes, config_bytes)

    config_path = tmp_path / "config.json"
    sig_path = tmp_path / "config.json.sig"
    config_path.write_bytes(config_bytes)
    sig_path.write_text(sig_hex)

    loaded = load_verified_config(str(config_path), str(sig_path), pub_bytes.hex())
    assert loaded == config


def test_startup_fails_when_signed_config_required_and_invalid(tmp_path, monkeypatch):
    priv_bytes, pub_bytes = _generate_keypair()
    config_bytes = b'{"key":"value"}'
    bad_sig = "00" * 64  # wrong signature

    config_path = tmp_path / "config.json"
    sig_path = tmp_path / "config.json.sig"
    config_path.write_bytes(config_bytes)
    sig_path.write_text(bad_sig)

    with pytest.raises(ConfigVerificationError):
        load_verified_config(str(config_path), str(sig_path), pub_bytes.hex())


def test_unsigned_config_allowed_when_requirement_disabled(monkeypatch):
    monkeypatch.delenv("PROXION_REQUIRE_SIGNED_CONFIG", raising=False)
    # Should be a no-op
    check_signed_config_startup(store=None)


def test_check_startup_raises_when_require_set_but_no_paths(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_SIGNED_CONFIG", "1")
    monkeypatch.delenv("PROXION_CONFIG_FILE", raising=False)
    monkeypatch.delenv("PROXION_CONFIG_SIG_FILE", raising=False)
    with pytest.raises(ConfigVerificationError):
        check_signed_config_startup(store=None)


def test_check_startup_succeeds_with_valid_config(tmp_path, monkeypatch):
    priv_bytes, pub_bytes = _generate_keypair()
    config_bytes = b'{"mode":"prod"}'
    sig_hex = _sign_config(priv_bytes, config_bytes)

    config_path = tmp_path / "c.json"
    sig_path = tmp_path / "c.json.sig"
    config_path.write_bytes(config_bytes)
    sig_path.write_text(sig_hex)

    monkeypatch.setenv("PROXION_REQUIRE_SIGNED_CONFIG", "1")
    monkeypatch.setenv("PROXION_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("PROXION_CONFIG_SIG_FILE", str(sig_path))
    monkeypatch.setenv("PROXION_CONFIG_PUBKEY_HEX", pub_bytes.hex())
    check_signed_config_startup(store=None)


def test_invalid_pubkey_raises_verification_error(tmp_path):
    config_bytes = b'{"x":1}'
    config_path = tmp_path / "c.json"
    sig_path = tmp_path / "c.sig"
    config_path.write_bytes(config_bytes)
    sig_path.write_text("aa" * 64)

    with pytest.raises(ConfigVerificationError):
        load_verified_config(str(config_path), str(sig_path), "notvalidhex!!!")


def test_missing_config_file_raises():
    with pytest.raises(FileNotFoundError):
        load_verified_config("/nonexistent/config.json", "/nonexistent/sig", "aa" * 32)
