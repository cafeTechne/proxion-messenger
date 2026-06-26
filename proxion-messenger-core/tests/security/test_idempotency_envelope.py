"""Tests for idempotency envelope (Round 20)."""
import pytest
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.idempotency import make_op_envelope, is_duplicate_operation


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_new_op_id_not_found(store):
    """An op_id that has never been recorded is not a duplicate."""
    env = make_op_envelope("send_dm", "alice@example.org")
    assert not is_duplicate_operation(store, env["op_id"])


def test_duplicate_op_id_returns_prior_result(store):
    """After recording an op_id, is_duplicate_operation returns True."""
    env = make_op_envelope("send_dm", "alice@example.org", "dev-1")
    store.record_operation_result(
        env["op_id"], env["op_type"], env["actor_webid"],
        env["actor_device_id"], "ok",
    )
    assert is_duplicate_operation(store, env["op_id"])
    result = store.get_operation_result(env["op_id"])
    assert result is not None
    assert result["result_code"] == "ok"
    assert result["actor_webid"] == "alice@example.org"


def test_idempotency_records_persist_across_store_reopen(tmp_path):
    """Records written to the DB survive a store close and reopen."""
    db_path = str(tmp_path / "idm.db")
    store = LocalStore(db_path)
    env = make_op_envelope("send_room", "bob@example.org")
    store.record_operation_result(
        env["op_id"], env["op_type"], env["actor_webid"], None, "sent",
    )

    store2 = LocalStore(db_path)
    assert is_duplicate_operation(store2, env["op_id"])
    result = store2.get_operation_result(env["op_id"])
    assert result["result_code"] == "sent"
