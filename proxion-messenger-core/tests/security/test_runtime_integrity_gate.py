"""R10: Runtime integrity gate tests."""
import json
import os
import pytest

from proxion_messenger_core.supply_chain import (
    verify_runtime_integrity,
    check_runtime_integrity_startup,
    IntegrityError,
)


def test_startup_allows_when_integrity_check_passes(monkeypatch):
    monkeypatch.delenv("PROXION_REQUIRE_RUNTIME_INTEGRITY", raising=False)
    # Should be a no-op
    check_runtime_integrity_startup(store=None)


def test_startup_aborts_when_runtime_integrity_required_and_fails(tmp_path, monkeypatch):
    # Bad manifest file with invalid hash
    manifest = {"files": {"/nonexistent/path/file.py": "aabbcc" * 10}}
    manifest_path = tmp_path / "manifest.json"
    manifest_bytes = json.dumps(manifest).encode()
    manifest_path.write_bytes(manifest_bytes)

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
    pub_bytes = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    sig = priv.sign(manifest_bytes)
    sig_path = tmp_path / "manifest.json.sig"
    sig_path.write_text(sig.hex())

    monkeypatch.setenv("PROXION_REQUIRE_RUNTIME_INTEGRITY", "1")
    monkeypatch.setenv("PROXION_MANIFEST_FILE", str(manifest_path))
    monkeypatch.setenv("PROXION_MANIFEST_SIG_FILE", str(sig_path))
    monkeypatch.setenv("PROXION_RUNTIME_PUBKEY_HEX", pub_bytes.hex())

    with pytest.raises(IntegrityError):
        check_runtime_integrity_startup(store=None)


def test_runtime_integrity_failure_emits_security_event(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    mock_store = MagicMock()

    manifest = {"files": {"/nonexistent/missing_file.py": "dead" * 16}}
    manifest_path = tmp_path / "m.json"
    manifest_bytes = json.dumps(manifest).encode()
    manifest_path.write_bytes(manifest_bytes)

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    sig = priv.sign(manifest_bytes)
    sig_path = tmp_path / "m.sig"
    sig_path.write_text(sig.hex())

    monkeypatch.setenv("PROXION_REQUIRE_RUNTIME_INTEGRITY", "1")
    monkeypatch.setenv("PROXION_MANIFEST_FILE", str(manifest_path))
    monkeypatch.setenv("PROXION_MANIFEST_SIG_FILE", str(sig_path))
    monkeypatch.setenv("PROXION_RUNTIME_PUBKEY_HEX", pub_bytes.hex())

    with pytest.raises(IntegrityError):
        check_runtime_integrity_startup(store=mock_store)

    mock_store.save_security_event.assert_called_once()
    call_args = mock_store.save_security_event.call_args
    assert call_args[0][0] == "runtime_integrity_failed"


def test_verify_runtime_integrity_passes_without_manifest(monkeypatch):
    monkeypatch.delenv("PROXION_MANIFEST_FILE", raising=False)
    monkeypatch.delenv("PROXION_MANIFEST_SIG_FILE", raising=False)
    result = verify_runtime_integrity(strict=False)
    assert result["passed"] is True
    assert result["manifest_verified"] is False


def test_verify_runtime_integrity_strict_raises_on_failure(tmp_path, monkeypatch):
    manifest = {"files": {"/nonexistent/bad.py": "ff" * 32}}
    manifest_bytes = json.dumps(manifest).encode()
    manifest_path = tmp_path / "m.json"
    manifest_path.write_bytes(manifest_bytes)

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    sig = priv.sign(manifest_bytes)
    sig_path = tmp_path / "m.sig"
    sig_path.write_text(sig.hex())

    monkeypatch.setenv("PROXION_MANIFEST_FILE", str(manifest_path))
    monkeypatch.setenv("PROXION_MANIFEST_SIG_FILE", str(sig_path))
    monkeypatch.setenv("PROXION_RUNTIME_PUBKEY_HEX", pub_bytes.hex())

    with pytest.raises(IntegrityError):
        verify_runtime_integrity(strict=True)
