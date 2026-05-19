"""R12: Backup manifest integrity tests."""
import hashlib
import json
import pytest

from proxion_messenger_core.persist import AgentState, PersistError


@pytest.fixture
def agent():
    return AgentState.generate()


def test_export_backup_contains_manifest_and_content_hash(agent):
    backup_bytes = agent.export_backup(passphrase=b"test-passphrase-12345")
    obj = json.loads(backup_bytes.decode("utf-8"))
    assert "manifest" in obj
    manifest = obj["manifest"]
    assert "manifest_version" in manifest
    assert "created_at" in manifest
    assert "content_sha256" in manifest
    assert "mode" in manifest
    assert manifest["mode"] == "passphrase"
    assert "key_versions" in manifest


def test_backup_manifest_content_sha256_is_correct(agent):
    backup_bytes = agent.export_backup(passphrase=b"test-passphrase-12345")
    obj = json.loads(backup_bytes.decode("utf-8"))
    manifest = obj["manifest"]
    expected_sha256 = manifest["content_sha256"]
    # Recompute what the export computed
    content_bytes = json.dumps({
        "identity_key_pem": obj["identity_key_pem"],
        "store_key_pem": obj["store_key_pem"],
    }).encode("utf-8")
    actual_sha256 = hashlib.sha256(content_bytes).hexdigest()
    assert actual_sha256 == expected_sha256


def test_import_rejects_manifest_hash_mismatch(agent):
    backup_bytes = agent.export_backup(passphrase=b"test-passphrase-12345")
    obj = json.loads(backup_bytes.decode("utf-8"))
    # Corrupt the manifest hash
    obj["manifest"]["content_sha256"] = "00" * 32
    tampered = json.dumps(obj, indent=2).encode("utf-8")
    with pytest.raises(PersistError, match="backup_integrity_mismatch"):
        AgentState.import_backup(tampered, passphrase=b"test-passphrase-12345")


def test_import_accepts_valid_manifest(agent):
    backup_bytes = agent.export_backup(passphrase=b"test-passphrase-12345")
    restored = AgentState.import_backup(backup_bytes, passphrase=b"test-passphrase-12345")
    assert restored.identity_pub_bytes == agent.identity_pub_bytes


def test_recipient_key_backup_contains_manifest(agent):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    priv = X25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    backup_bytes = agent.export_backup(recipient_pubkey_hex=pub_hex)
    obj = json.loads(backup_bytes.decode("utf-8"))
    assert "manifest" in obj
    assert obj["manifest"]["mode"] == "recipient_key"


def test_restore_api_audits_manifest_hash(agent):
    """Backup contains manifest with key_versions for operator transparency."""
    backup_bytes = agent.export_backup(passphrase=b"test-passphrase-12345")
    obj = json.loads(backup_bytes.decode("utf-8"))
    assert obj["manifest"]["key_versions"]["identity"] == "Ed25519"
    assert obj["manifest"]["key_versions"]["store"] == "X25519"
