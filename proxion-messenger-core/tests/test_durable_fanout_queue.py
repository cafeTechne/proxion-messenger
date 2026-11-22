"""Acceptance is committed before ack; registration only removes sent rows."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    db = LocalStore(str(tmp_path / "queue.db"))
    yield db
    db.close()


def enqueue(store, message="m", sender="sender", owner="owner", device="device", payload=None):
    return store.enqueue_device_fanout(message, sender, owner, device, payload or {"content": "ciphertext", "e2e": True})


def test_queue_survives_store_restart_in_fifo_order(tmp_path):
    path = str(tmp_path / "restart.db")
    first = LocalStore(path)
    for i in range(3):
        assert enqueue(first, str(i))
    first.close()
    second = LocalStore(path)
    try:
        rows = second.pending_device_fanout("owner", "device")
        assert [row["message_id"] for row in rows] == ["0", "1", "2"]
        assert rows[0]["payload"] == {"content": "ciphertext", "e2e": True}
    finally:
        second.close()


def test_schema_65_upgrade_preserves_existing_data(tmp_path):
    path = str(tmp_path / "upgrade.db")
    old = LocalStore(path)
    old.save_message("existing", "thread", "local_dm", "alice", "Alice", "old", "2026-01-01")
    old.close()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE pending_device_fanout")
        conn.execute("DROP TABLE committed_device_fanout")
        conn.execute("UPDATE schema_version SET version=65")
    upgraded = LocalStore(path)
    try:
        assert enqueue(upgraded)
        assert upgraded.get_messages("thread")[0]["message_id"] == "existing"
        with upgraded._conn() as conn:
            assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 67
    finally:
        upgraded.close()


def test_same_identity_id_conflict_cannot_replace_ciphertext(store):
    assert enqueue(store, payload={"content": "ciphertext", "nonce": "n"})
    assert enqueue(store, payload={"nonce": "n", "content": "ciphertext"})
    assert not enqueue(store, payload={"content": "changed", "nonce": "n"})
    assert enqueue(store, sender="another-sender")
    rows = store.pending_device_fanout("owner", "device")
    assert len(rows) == 2
    assert rows[0]["payload"]["content"] == "ciphertext"


def test_owner_and_device_scope_for_reads_and_removals(store):
    assert enqueue(store)
    assert store.pending_device_fanout("foreign", "device") == []
    assert store.pending_device_fanout("owner", "sibling") == []
    row = store.pending_device_fanout("owner", "device")[0]
    assert not store.remove_device_fanout("foreign", "device", row["queue_id"])
    assert not store.remove_device_fanout("owner", "sibling", row["queue_id"])
    assert store.remove_device_fanout("owner", "device", row["queue_id"])
    assert store.pending_device_fanout("owner", "device") == []


def test_concurrent_enqueue_respects_pending_cap_without_eviction(store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        accepted = list(pool.map(lambda index: enqueue(store, str(index)), range(110)))
    assert sum(accepted) == 100
    rows = store.pending_device_fanout("owner", "device")
    assert len(rows) == 100
    assert not enqueue(store, "extra")
    assert store.pending_device_fanout("owner", "device") == rows


def test_byte_capacity_refuses_new_rows_but_accepts_exact_retry(store, monkeypatch):
    assert enqueue(store)
    with store._conn() as conn:
        size = conn.execute("SELECT SUM(byte_count) FROM pending_device_fanout").fetchone()[0]
    monkeypatch.setattr(store, "MAX_FANOUT_BYTES", size)
    assert enqueue(store)
    assert not enqueue(store, "extra")
    assert len(store.pending_device_fanout("owner", "device")) == 1


def test_block_purges_only_blocked_sender_and_prevents_requeue(store):
    assert enqueue(store)
    assert enqueue(store, "other", sender="other")
    assert enqueue(store, "foreign-owner", owner="foreign")
    store.set_block("owner", "sender", True)
    assert [row["from_webid"] for row in store.pending_device_fanout("owner", "device")] == ["other"]
    assert not enqueue(store)
    assert len(store.pending_device_fanout("foreign", "device")) == 1


def test_device_revocation_removes_queued_data_and_prevents_new_acceptance(store):
    assert enqueue(store, device="did:key:device")
    assert enqueue(store, "sibling", device="did:key:sibling")
    assert store.register_device("did:key:device", "owner", "pub", "attestation")
    assert not store.revoke_registered_device("foreign", "did:key:device")
    assert store.revoke_registered_device("owner", "did:key:device")
    assert store.pending_device_fanout("owner", "did:key:device") == []
    assert not enqueue(store, device="did:key:device")
    assert len(store.pending_device_fanout("owner", "did:key:sibling")) == 1


def test_account_deletion_purges_owned_and_authored_pending_data(store):
    assert enqueue(store)
    assert enqueue(store, "authored", sender="owner", owner="other")
    assert enqueue(store, "unrelated", sender="third", owner="other")
    assert store.delete_account_data("owner")["pending_device_fanout"] == 2
    assert store.pending_device_fanout("owner", "device") == []
    assert [row["message_id"] for row in store.pending_device_fanout("other", "device")] == ["unrelated"]


def test_transaction_failure_never_commits_partial_acceptance(store):
    with pytest.raises(RuntimeError):
        with store.transaction():
            assert enqueue(store)
            raise RuntimeError("crash before commit")
    assert store.pending_device_fanout("owner", "device") == []


def test_payload_and_read_work_are_bounded(store):
    assert not enqueue(store, payload={"content": "x" * 131073})
    with pytest.raises(ValueError):
        store.pending_device_fanout("owner", "device", limit=101)


def test_distinct_device_cap_preserves_existing_queues(store):
    for index in range(500):
        assert enqueue(store, owner=f"owner-{index}")
    assert not enqueue(store, owner="over-cap")
    assert enqueue(store, owner="owner-0")  # an exact retry still fits
    assert len(store.pending_device_fanout("owner-499", "device")) == 1
