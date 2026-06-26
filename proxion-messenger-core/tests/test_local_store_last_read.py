"""Tests for LocalStore.get_last_read, get_all_last_reads, get_messages_by_ids."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_get_last_read_returns_zero_when_unset(store):
    result = store.get_last_read("did:key:alice", "room-abc")
    assert result == 0.0


def test_set_get_last_read_roundtrip(store):
    before = time.time()
    store.set_last_read("did:key:alice", "room-abc")
    after = time.time()
    result = store.get_last_read("did:key:alice", "room-abc")
    assert before <= result <= after


def test_get_all_last_reads_returns_all_channels(store):
    store.set_last_read("did:key:alice", "room-1")
    store.set_last_read("did:key:alice", "room-2")
    store.set_last_read("did:key:alice", "room-3")
    reads = store.get_all_last_reads("did:key:alice")
    assert set(reads.keys()) == {"room-1", "room-2", "room-3"}
    for ts in reads.values():
        assert ts > 0


def test_get_all_last_reads_empty_for_unknown_webid(store):
    reads = store.get_all_last_reads("did:key:nobody")
    assert reads == {}


def test_get_messages_by_ids_returns_subset(store):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store.save_message("msg-1", "room-x", "room", "did:key:alice", "Alice", "hello", now)
    store.save_message("msg-2", "room-x", "room", "did:key:bob", "Bob", "world", now)
    store.save_message("msg-3", "room-x", "room", "did:key:alice", "Alice", "!", now)

    rows = store.get_messages_by_ids(["msg-1", "msg-3"])
    ids = {r["message_id"] for r in rows}
    assert ids == {"msg-1", "msg-3"}
    assert "msg-2" not in ids


def test_get_messages_by_ids_empty_list(store):
    assert store.get_messages_by_ids([]) == []


def test_get_messages_by_ids_unknown_id(store):
    assert store.get_messages_by_ids(["no-such-id"]) == []
