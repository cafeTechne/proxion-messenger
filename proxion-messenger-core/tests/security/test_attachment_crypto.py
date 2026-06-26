"""Tests for file attachment E2E encryption (Round 19)."""
import os
import pytest
from cryptography.exceptions import InvalidTag
from proxion_messenger_core.attachment_crypto import (
    encrypt_attachment,
    decrypt_attachment,
    attachment_key_payload,
)


def test_encrypt_decrypt_attachment_roundtrip():
    """Encrypting and then decrypting must recover the original bytes."""
    data = b"Hello, this is a test file payload \x00\xff" * 100
    enc = encrypt_attachment(data)
    recovered = decrypt_attachment(enc["ciphertext_b64"], enc["key_b64"], enc["nonce_b64"])
    assert recovered == data
    assert enc["size"] == len(data)


def test_different_files_produce_different_ciphertext():
    """Each encrypt_attachment call must use a fresh random key and nonce."""
    data = b"same content"
    enc1 = encrypt_attachment(data)
    enc2 = encrypt_attachment(data)
    # Different nonces → different ciphertexts
    assert enc1["nonce_b64"] != enc2["nonce_b64"]
    assert enc1["ciphertext_b64"] != enc2["ciphertext_b64"]
    # But both should decrypt correctly
    assert decrypt_attachment(enc1["ciphertext_b64"], enc1["key_b64"], enc1["nonce_b64"]) == data
    assert decrypt_attachment(enc2["ciphertext_b64"], enc2["key_b64"], enc2["nonce_b64"]) == data


def test_tampered_attachment_raises():
    """Modifying the ciphertext must cause decryption to raise InvalidTag."""
    data = b"sensitive file bytes"
    enc = encrypt_attachment(data)

    import base64
    ct = base64.b64decode(enc["ciphertext_b64"])
    # Flip a byte in the ciphertext
    tampered = bytearray(ct)
    tampered[0] ^= 0xFF
    tampered_b64 = base64.b64encode(bytes(tampered)).decode()

    with pytest.raises(InvalidTag):
        decrypt_attachment(tampered_b64, enc["key_b64"], enc["nonce_b64"])


def test_attachment_key_payload_roundtrip():
    """attachment_key_payload returns a dict with all expected fields."""
    enc = encrypt_attachment(b"test data")
    payload = attachment_key_payload(
        enc["key_b64"], enc["nonce_b64"], "report.pdf", "application/pdf"
    )
    assert payload["type"] == "attachment_key"
    assert payload["key_b64"] == enc["key_b64"]
    assert payload["nonce_b64"] == enc["nonce_b64"]
    assert payload["filename"] == "report.pdf"
    assert payload["mime_type"] == "application/pdf"
    # Verify the key in the payload actually decrypts the ciphertext
    recovered = decrypt_attachment(enc["ciphertext_b64"], payload["key_b64"], payload["nonce_b64"])
    assert recovered == b"test data"
