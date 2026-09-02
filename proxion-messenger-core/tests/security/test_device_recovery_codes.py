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


def _make_code(store, owner):
    import secrets
    code_id = str(uuid.uuid4())
    plaintext = secrets.token_hex(16)
    code_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    store.save_device_recovery_code(code_id, owner, code_hash)
    return code_id


def _set_expires_at(store, code_id, expires_at):
    with store._conn() as conn:
        conn.execute(
            "UPDATE device_recovery_codes SET expires_at=? WHERE code_id=?",
            (expires_at, code_id),
        )


def test_save_sets_expires_at_from_ttl(store):
    from proxion_messenger_core._store.devices import DEVICE_RECOVERY_CODE_TTL_SECONDS

    code_id = _make_code(store, "did:web:dave.example")
    record = store.get_device_recovery_code(code_id)

    assert record["expires_at"] is not None
    assert record["expires_at"] > record["created_at"]
    assert record["expires_at"] == pytest.approx(
        record["created_at"] + DEVICE_RECOVERY_CODE_TTL_SECONDS, abs=1.0
    )


def test_fresh_recovery_code_still_redeems(store):
    code_id = _make_code(store, "did:web:erin.example")
    assert store.use_device_recovery_code(code_id) is True


def test_expired_recovery_code_is_rejected(store):
    import time

    code_id = _make_code(store, "did:web:frank.example")
    _set_expires_at(store, code_id, time.time() - 1)

    assert store.use_device_recovery_code(code_id) is False
    # Still unused: rejection must not consume the code.
    assert store.get_device_recovery_code(code_id)["used_at"] is None


def test_prune_removes_expired_unused_codes_only(store):
    import time

    expired = _make_code(store, "did:web:grace.example")
    _set_expires_at(store, expired, time.time() - 1)
    fresh = _make_code(store, "did:web:heidi.example")
    used = _make_code(store, "did:web:ivan.example")
    store.use_device_recovery_code(used)  # redeemed while still fresh
    _set_expires_at(store, used, time.time() - 1)

    removed = store.prune_expired_device_recovery_codes()

    assert removed == 1
    assert store.get_device_recovery_code(expired) is None
    assert store.get_device_recovery_code(fresh) is not None
    assert store.get_device_recovery_code(used) is not None
