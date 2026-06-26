"""Tests for device recovery code generation and single-use enforcement (Schema v47)."""
import hashlib
import uuid

import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_recovery_code_generated_and_hashed(store):
    import secrets
    code_id = str(uuid.uuid4())
    plaintext = secrets.token_hex(16)
    code_hash = hashlib.sha256(plaintext.encode()).hexdigest()

    store.save_device_recovery_code(code_id, "did:web:alice.example", code_hash)
    record = store.get_device_recovery_code(code_id)

    assert record is not None
    assert record["code_hash"] == code_hash
    assert record["used_at"] is None
    assert hashlib.sha256(plaintext.encode()).hexdigest() == record["code_hash"]


def test_recovery_code_single_use_enforced(store):
    import secrets
    code_id = str(uuid.uuid4())
    plaintext = secrets.token_hex(16)
    code_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    store.save_device_recovery_code(code_id, "did:web:bob.example", code_hash)

    first_use = store.use_device_recovery_code(code_id)
    assert first_use is True

    record = store.get_device_recovery_code(code_id)
    assert record["used_at"] is not None


def test_used_recovery_code_cannot_be_reused(store):
    import secrets
    code_id = str(uuid.uuid4())
    plaintext = secrets.token_hex(16)
    code_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    store.save_device_recovery_code(code_id, "did:web:charlie.example", code_hash)

    store.use_device_recovery_code(code_id)
    second_use = store.use_device_recovery_code(code_id)
    assert second_use is False
