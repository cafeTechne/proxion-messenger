"""Tests for idempotency TTL cleanup and prune_expired_idempotency_ops."""
import time
import uuid

import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_replayed_op_id_returns_prior_result(store):
    op_id = str(uuid.uuid4())

    assert store.get_operation_result(op_id) is None
    store.record_operation_result(op_id, "send_dm", "did:web:alice.example", None, "ok")
    result = store.get_operation_result(op_id)
    assert result is not None
    assert result["result_code"] == "ok"


def test_idempotency_ttl_cleanup_removes_old_records(store):
    op_id = str(uuid.uuid4())
    store.record_operation_result(op_id, "send_dm", "did:web:alice.example", None, "ok")

    with store._conn() as conn:
        conn.execute(
            "UPDATE idempotency_ops SET created_at=? WHERE op_id=?",
            (time.time() - 73 * 3600, op_id),
        )

    pruned = store.prune_expired_idempotency_ops(retention_hours=72)
    assert pruned >= 1
    assert store.get_operation_result(op_id) is None


def test_active_window_records_preserved_during_cleanup(store):
    fresh_id = str(uuid.uuid4())
    store.record_operation_result(fresh_id, "send_dm", "did:web:alice.example", None, "ok")

    store.prune_expired_idempotency_ops(retention_hours=72)

    result = store.get_operation_result(fresh_id)
    assert result is not None and result["result_code"] == "ok"
