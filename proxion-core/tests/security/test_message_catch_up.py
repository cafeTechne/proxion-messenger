"""Tests for message sequence numbers and offline catch-up (Round 19)."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _insert_message(store, thread_id, message_id, content="hello", seq=None):
    """Helper: insert a message with an optional seq value."""
    import sqlite3
    ts = time.time()
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        """INSERT INTO messages
           (message_id, thread_id, thread_type, from_webid, content, timestamp, seq)
           VALUES (?, ?, 'room', 'alice@example.org', ?, datetime('now'), ?)""",
        (message_id, thread_id, content, seq),
    )
    conn.commit()
    conn.close()


def test_messages_assigned_seq_numbers(store):
    """get_next_seq returns incrementing integers starting from 1."""
    thread_id = "room-alpha"
    seqs = [store.get_next_seq(thread_id) for _ in range(5)]
    assert seqs == [1, 2, 3, 4, 5]


def test_catch_up_returns_messages_since_seq(store):
    """get_messages_since_seq returns messages with seq > given value."""
    thread_id = "room-beta"
    for i in range(1, 6):
        _insert_message(store, thread_id, f"msg-{i}", f"content {i}", seq=i)

    # Client has seen up to seq 2; should get 3, 4, 5
    msgs = store.get_messages_since_seq(thread_id, since_seq=2)
    assert len(msgs) == 3
    seqs = [m["seq"] for m in msgs]
    assert seqs == [3, 4, 5]


def test_catch_up_empty_when_up_to_date(store):
    """get_messages_since_seq returns empty list when no new messages."""
    thread_id = "room-gamma"
    for i in range(1, 4):
        _insert_message(store, thread_id, f"msg-g-{i}", seq=i)

    msgs = store.get_messages_since_seq(thread_id, since_seq=3)
    assert msgs == []


def test_catch_up_batch_ordered_ascending(store):
    """get_messages_since_seq returns messages in ascending seq order."""
    thread_id = "room-delta"
    # Insert in reverse order
    for i in [5, 3, 1, 4, 2]:
        _insert_message(store, thread_id, f"msg-d-{i}", seq=i)

    msgs = store.get_messages_since_seq(thread_id, since_seq=0)
    seqs = [m["seq"] for m in msgs]
    assert seqs == sorted(seqs)
    assert seqs == [1, 2, 3, 4, 5]
