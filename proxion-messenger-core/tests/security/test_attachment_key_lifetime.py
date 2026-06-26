"""Tests for attachment key lifetime controls (Round 20)."""
import time
import pytest
from proxion_messenger_core.attachment_crypto import (
    encrypt_attachment,
    decrypt_attachment,
    is_attachment_key_expired,
)


def test_encrypt_attachment_includes_expires_at():
    """encrypt_attachment returns an expires_at timestamp in the future."""
    enc = encrypt_attachment(b"test payload")
    assert "expires_at" in enc
    assert enc["expires_at"] > time.time()


def test_attachment_key_not_expired_when_fresh():
    """A freshly generated attachment key with default TTL is not expired."""
    enc = encrypt_attachment(b"fresh data")
    assert not is_attachment_key_expired(enc)


def test_attachment_key_expired_after_ttl_elapsed():
    """is_attachment_key_expired returns True when expires_at is in the past."""
    enc = encrypt_attachment(b"old data", ttl_seconds=1)
    # Manually backdate expires_at
    expired_payload = {**enc, "expires_at": time.time() - 1}
    assert is_attachment_key_expired(expired_payload)
