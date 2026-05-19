"""R10: Backup recipient-key mode tests."""
import json
import pytest

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from proxion_messenger_core.persist import AgentState, PersistError


@pytest.fixture
def agent():
    return AgentState.generate()


def test_backup_rejects_invalid_recipient_pubkey(agent):
    """export_backup with garbage recipient_pubkey_hex raises PersistError."""
    with pytest.raises(PersistError, match="Invalid recipient_pubkey_hex"):
        agent.export_backup(recipient_pubkey_hex="not_valid_hex!!!")


def test_backup_recipient_mode_envelope_metadata_present(agent):
    """Recipient-key envelope must contain required fields."""
    recipient_priv = X25519PrivateKey.generate()
    recipient_pub_bytes = recipient_priv.public_key().public_bytes(
        encoding=__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.Raw,
        format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["PublicFormat"]).PublicFormat.Raw,
    )
    blob = agent.export_backup(recipient_pubkey_hex=recipient_pub_bytes.hex())
    obj = json.loads(blob.decode("utf-8"))
    assert obj["@type"] == "ProxionBackup"
    assert obj["backup_mode"] == "recipient_key"
    assert "ephemeral_pub_hex" in obj
    assert "nonce_hex" in obj
    assert "ciphertext_hex" in obj
    assert obj["version"] == 1


def test_passphrase_and_recipient_mode_mutually_exclusive(agent):
    """export_backup must reject requests that provide both passphrase and recipient_pubkey_hex."""
    recipient_priv = X25519PrivateKey.generate()
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    pub_bytes = recipient_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    with pytest.raises(PersistError, match="mutually exclusive"):
        agent.export_backup(
            passphrase=b"somepassphrase",
            recipient_pubkey_hex=pub_bytes.hex(),
        )


def test_backup_recipient_key_roundtrip(agent):
    """Backup encrypted with recipient public key must be decryptable with matching private key."""
    recipient_priv = X25519PrivateKey.generate()
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    pub_bytes = recipient_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    blob = agent.export_backup(recipient_pubkey_hex=pub_bytes.hex())
    restored = AgentState.import_backup(blob, recipient_privkey=recipient_priv)
    assert restored.identity_pub_bytes == agent.identity_pub_bytes
