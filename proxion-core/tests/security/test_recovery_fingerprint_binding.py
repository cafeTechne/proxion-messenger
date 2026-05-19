"""R9: Recovery operation fingerprint binding tests."""
import hashlib
import time
import uuid
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _fingerprint(ip: str, op_type: str) -> str:
    return hashlib.sha256(f"{ip}|{op_type}".encode()).hexdigest()


def _prepare_op(store, op_type="restore", ip="127.0.0.1"):
    op_id = str(uuid.uuid4())
    now = time.time()
    fp = _fingerprint(ip, op_type)
    store.create_recovery_operation(
        op_id=op_id,
        op_type=op_type,
        requested_by="did:key:owner",
        requested_at=now,
        expires_at=now + 300,
        requester_fingerprint=fp,
    )
    store.confirm_recovery_operation(op_id, now)
    return op_id, fp


def test_recovery_op_succeeds_on_matching_fingerprint(store):
    op_id, stored_fp = _prepare_op(store, ip="192.168.1.1")
    req_fp = _fingerprint("192.168.1.1", "restore")
    assert req_fp == stored_fp
    consumed = store.consume_recovery_operation(op_id)
    assert consumed is True


def test_recovery_op_rejected_on_fingerprint_mismatch(store):
    op_id, stored_fp = _prepare_op(store, ip="192.168.1.1")
    req_fp = _fingerprint("10.0.0.1", "restore")  # different IP
    assert req_fp != stored_fp


def test_recovery_op_single_use_enforced_with_consumed_at(store):
    op_id, _ = _prepare_op(store, ip="127.0.0.1")
    first = store.consume_recovery_operation(op_id)
    assert first is True
    second = store.consume_recovery_operation(op_id)
    assert second is False


def test_consumed_at_is_recorded(store):
    op_id, _ = _prepare_op(store)
    store.consume_recovery_operation(op_id)
    op = store.get_recovery_operation(op_id)
    assert op is not None
    assert op["consumed_at"] is not None
    assert op["consumed_at"] > 0


def test_fingerprint_stored_in_db(store):
    op_id, fp = _prepare_op(store, ip="1.2.3.4")
    op = store.get_recovery_operation(op_id)
    assert op["requester_fingerprint"] == fp


def test_op_without_fingerprint_still_works(store):
    op_id = str(uuid.uuid4())
    now = time.time()
    store.create_recovery_operation(
        op_id=op_id,
        op_type="restore",
        requested_by="did:key:owner",
        requested_at=now,
        expires_at=now + 300,
        requester_fingerprint=None,
    )
    store.confirm_recovery_operation(op_id, now)
    consumed = store.consume_recovery_operation(op_id)
    assert consumed is True
