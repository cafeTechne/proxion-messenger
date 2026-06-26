"""Tests for dm_session_recovery_attempts table and store methods (Schema v47)."""
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_recovery_attempt_created_with_pending_status(store):
    store.record_recovery_attempt("thread-1", "session-abc", "did:web:alice.example", attempt_no=1)
    attempts = store.get_recovery_attempts("thread-1", "did:web:alice.example")
    assert len(attempts) == 1
    assert attempts[0]["status"] == "pending"
    assert attempts[0]["attempt_no"] == 1


def test_recovery_attempt_status_updated(store):
    store.record_recovery_attempt("thread-2", "session-xyz", "did:web:bob.example", attempt_no=1)
    store.update_recovery_attempt("thread-2", "did:web:bob.example", 1, "sent")
    attempts = store.get_recovery_attempts("thread-2", "did:web:bob.example")
    assert attempts[0]["status"] == "sent"


def test_recovery_attempts_persist_in_schema_v47_table(store):
    assert store._SCHEMA_VERSION >= 47
    store.record_recovery_attempt("thread-3", None, "did:web:charlie.example", attempt_no=1)
    store.record_recovery_attempt("thread-3", None, "did:web:charlie.example", attempt_no=2)
    attempts = store.get_recovery_attempts("thread-3", "did:web:charlie.example")
    assert len(attempts) == 2
    attempt_numbers = {a["attempt_no"] for a in attempts}
    assert attempt_numbers == {1, 2}
