"""Tests for LocalStore.update_message, remove_room_member, and search_messages."""
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_update_message_changes_content(store):
    store.save_message("m1", "thread-a", "dm", "alice", "Alice", "original", "2026-04-16T10:00:00+00:00")
    store.update_message("m1", "updated")
    msgs = store.get_messages("thread-a")
    assert msgs[0]["content"] == "updated"


def test_update_message_nonexistent_is_safe(store):
    # Should not raise
    store.update_message("does-not-exist", "anything")


def test_remove_room_member_removes_entry(store):
    store.save_room("room-1", "Room One", "abc", "", "none")
    store.add_room_member("room-1", "did:key:alice")
    store.add_room_member("room-1", "did:key:bob")
    assert "did:key:alice" in store.get_room_members("room-1")

    store.remove_room_member("room-1", "did:key:alice")
    members = store.get_room_members("room-1")
    assert "did:key:alice" not in members
    assert "did:key:bob" in members


def test_remove_room_member_nonexistent_is_safe(store):
    store.save_room("room-2", "Room Two", "def", "", "none")
    store.remove_room_member("room-2", "did:key:nobody")  # should not raise


def test_search_messages_basic(store):
    store.save_message("m2", "thread-b", "dm", "alice", "Alice", "hello world", "2026-04-16T10:01:00+00:00")
    store.save_message("m3", "thread-b", "dm", "alice", "Alice", "goodbye", "2026-04-16T10:02:00+00:00")
    results = store.search_messages("hello")
    assert len(results) == 1
    assert results[0]["message_id"] == "m2"


def test_search_messages_case_insensitive(store):
    store.save_message("m4", "thread-c", "dm", "alice", "Alice", "Hello World", "2026-04-16T10:00:00+00:00")
    results = store.search_messages("hello")
    assert len(results) == 1


def test_search_messages_thread_filter(store):
    store.save_message("m5", "thread-d", "dm", "alice", "Alice", "find me", "2026-04-16T10:00:00+00:00")
    store.save_message("m6", "thread-e", "dm", "alice", "Alice", "find me too", "2026-04-16T10:00:00+00:00")
    results_all = store.search_messages("find me")
    assert len(results_all) == 2
    results_filtered = store.search_messages("find me", thread_id="thread-d")
    assert len(results_filtered) == 1
    assert results_filtered[0]["message_id"] == "m5"


def test_search_messages_no_results(store):
    store.save_message("m7", "thread-f", "dm", "alice", "Alice", "nothing relevant", "2026-04-16T10:00:00+00:00")
    results = store.search_messages("xyzzy_not_found")
    assert results == []
