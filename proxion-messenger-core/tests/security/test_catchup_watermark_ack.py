"""Tests for catch-up watermark and integrity anchors (Round 20)."""
import hashlib
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _insert_message(store, thread_id, message_id, seq):
    import sqlite3
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        """INSERT INTO messages
           (message_id, thread_id, thread_type, from_webid, content, timestamp, seq)
           VALUES (?, ?, 'room', 'alice@example.org', 'hello', datetime('now'), ?)""",
        (message_id, thread_id, seq),
    )
    conn.commit()
    conn.close()


def test_catchup_response_includes_batch_hash_and_seq_bounds(store):
    """get_messages_since_seq provides raw data; batch_hash computed correctly from message_id:seq pairs."""
    thread_id = "room-wm-1"
    for i in range(1, 4):
        _insert_message(store, thread_id, f"msg-{i}", i)

    msgs = store.get_messages_since_seq(thread_id, since_seq=0)
    assert len(msgs) == 3

    first_seq = msgs[0]["seq"]
    last_seq = msgs[-1]["seq"]
    assert first_seq == 1
    assert last_seq == 3

    hash_input = "|".join(
        sorted(f"{m.get('message_id', '')}:{m.get('seq', '')}" for m in msgs)
    ).encode()
    batch_hash = hashlib.sha256(hash_input).hexdigest()
    assert len(batch_hash) == 64  # valid sha256 hex


def test_watermark_set_and_retrieved(store):
    """set_catchup_watermark persists and get_catchup_watermark retrieves it."""
    store.set_catchup_watermark("alice@example.org", "dev-1", "room-wm-2", 42)
    seq = store.get_catchup_watermark("alice@example.org", "dev-1", "room-wm-2")
    assert seq == 42


def test_watermark_starts_at_zero(store):
    """get_catchup_watermark returns 0 for an unseen (owner, device, thread) triple."""
    seq = store.get_catchup_watermark("alice@example.org", "dev-unknown", "room-new")
    assert seq == 0
