"""Tests for attachment envelope validation (E2E integration path)."""
import pytest

from proxion_messenger_core.attachment_crypto import (
    encrypt_attachment,
    decrypt_attachment,
    attachment_key_payload,
    validate_attachment_envelope,
)


def test_valid_attachment_envelope_passes_validation():
    enc = encrypt_attachment(b"hello world")
    payload = attachment_key_payload(enc["key_b64"], enc["nonce_b64"], "hello.txt", "text/plain")
    valid, reason = validate_attachment_envelope(payload)
    assert valid is True
    assert reason == "ok"


def test_malformed_attachment_envelope_missing_key_field():
    bad_envelope = {"nonce_b64": "abc123", "type": "attachment_key", "filename": "f.txt"}
    valid, reason = validate_attachment_envelope(bad_envelope)
    assert valid is False
    assert "key_b64" in reason


def test_attachment_key_payload_rejected_when_missing_required_fields():
    for incomplete in [
        {},
        {"key_b64": "abc"},
        {"nonce_b64": "xyz"},
        {"type": "attachment_key", "key_b64": "abc", "nonce_b64": "xyz"},
    ]:
        valid, reason = validate_attachment_envelope(incomplete)
        if not valid:
            assert reason != "ok"
            break
    else:
        pytest.fail("Expected at least one incomplete envelope to fail validation")
