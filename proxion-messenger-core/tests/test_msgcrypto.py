"""Unit tests for msgcrypto.py."""

import pytest
from unittest.mock import MagicMock
from cryptography.exceptions import InvalidTag

from proxion_messenger_core.msgcrypto import (
    derive_message_key,
    encrypt_message,
    decrypt_message,
    is_encrypted,
)


@pytest.fixture
def mock_cert():
    """Mock RelationshipCertificate."""
    cert = MagicMock()
    cert.cert_bytes = b"test-cert-bytes-for-key-derivation"
    return cert


def test_derive_message_key(mock_cert):
    """Test derive_message_key produces consistent 32-byte key."""
    key1 = derive_message_key(mock_cert)
    key2 = derive_message_key(mock_cert)
    
    assert isinstance(key1, bytes)
    assert len(key1) == 32
    assert key1 == key2  # Same cert should produce same key


def test_encrypt_decrypt_roundtrip(mock_cert):
    """Test encrypt and decrypt roundtrip."""
    plaintext = "Hello, encrypted world!"
    key = derive_message_key(mock_cert)
    
    encrypted = encrypt_message(plaintext, key)
    assert encrypted.startswith("enc1:")
    
    decrypted = decrypt_message(encrypted, key)
    assert decrypted == plaintext


def test_is_encrypted():
    """Test is_encrypted detection."""
    assert is_encrypted("enc1:someciphertext")
    assert not is_encrypted("plain text message")
    assert not is_encrypted("")


def test_decrypt_unencrypted_passthrough():
    """Test decrypt returns unencrypted messages as-is."""
    plaintext = "This is not encrypted"
    key = b"doesnt-matter" * 2 + b"12"  # 32 bytes
    
    result = decrypt_message(plaintext, key)
    assert result == plaintext


def test_encrypt_wrong_key_fails(mock_cert):
    """Test decrypt with wrong key raises InvalidTag."""
    plaintext = "Secret message"
    key1 = derive_message_key(mock_cert)
    
    encrypted = encrypt_message(plaintext, key1)
    
    # Use a different key
    mock_cert.cert_bytes = b"different-cert-bytes"
    key2 = derive_message_key(mock_cert)
    
    with pytest.raises(InvalidTag):
        decrypt_message(encrypted, key2)


def test_encrypt_unicode():
    """Test encryption handles Unicode correctly."""
    plaintext = "Unicode test: 你好 世界 🌍"
    key = b"a" * 32
    
    encrypted = encrypt_message(plaintext, key)
    decrypted = decrypt_message(encrypted, key)
    
    assert decrypted == plaintext


def test_encrypt_nonce_uniqueness(mock_cert):
    """Test each encryption generates a unique nonce."""
    key = derive_message_key(mock_cert)
    plaintext = "Same message"
    
    encrypted1 = encrypt_message(plaintext, key)
    encrypted2 = encrypt_message(plaintext, key)
    
    # Different ciphertexts because of different nonces
    assert encrypted1 != encrypted2
    
    # But both decrypt to same plaintext
    assert decrypt_message(encrypted1, key) == plaintext
    assert decrypt_message(encrypted2, key) == plaintext


def test_decrypt_corrupted_ciphertext(mock_cert):
    """Test decrypt with corrupted ciphertext raises error."""
    key = derive_message_key(mock_cert)
    
    # Valid encrypted message
    encrypted = encrypt_message("test", key)
    
    # Corrupt it by changing one character
    corrupted = encrypted[:-5] + "xxxxx"
    
    with pytest.raises(InvalidTag):
        decrypt_message(corrupted, key)
