"""R12: Thread participant binding enforcement tests."""
import time
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_upsert_and_lookup_thread_binding(store):
    store.upsert_thread_participant_binding("thread-1", "did:key:alice", "dm")
    assert store.is_thread_participant_binding("thread-1", "did:key:alice") is True


def test_non_participant_cannot_access_thread(store):
    store.upsert_thread_participant_binding("thread-1", "did:key:alice", "dm")
    assert store.is_thread_participant_binding("thread-1", "did:key:bob") is False


def test_non_participant_cannot_mutate_thread(store):
    """Binding not present → non-participant cannot mutate."""
    store.upsert_thread_participant_binding("thread-2", "did:key:alice", "room")
    result = store.is_thread_participant_binding("thread-2", "did:key:attacker")
    assert result is False


def test_non_participant_cannot_read_thread(store):
    store.upsert_thread_participant_binding("thread-3", "did:key:alice", "dm")
    store.upsert_thread_participant_binding("thread-3", "did:key:bob", "dm")
    # Third party has no binding
    assert store.is_thread_participant_binding("thread-3", "did:key:carol") is False


def test_stale_membership_state_does_not_bypass_scope_check(store):
    """Upsert a binding, then check a different identity — binding is authoritative."""
    store.upsert_thread_participant_binding("thread-4", "did:key:alice", "dm")
    # did:key:stale_socket has no binding in thread_participant_bindings
    # regardless of any in-memory socket state
    assert store.is_thread_participant_binding("thread-4", "did:key:stale_socket") is False


def test_get_thread_participants_returns_all_bound(store):
    store.upsert_thread_participant_binding("thread-5", "did:key:alice", "room")
    store.upsert_thread_participant_binding("thread-5", "did:key:bob", "room")
    store.upsert_thread_participant_binding("thread-5", "did:key:carol", "room")
    participants = store.get_thread_participants("thread-5")
    assert set(participants) == {"did:key:alice", "did:key:bob", "did:key:carol"}


def test_upsert_updates_existing_binding(store):
    store.upsert_thread_participant_binding("thread-6", "did:key:alice", "dm")
    # Upsert again with different source — should not raise and binding should exist
    store.upsert_thread_participant_binding("thread-6", "did:key:alice", "room")
    assert store.is_thread_participant_binding("thread-6", "did:key:alice") is True
