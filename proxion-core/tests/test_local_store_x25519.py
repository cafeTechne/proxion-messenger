"""Tests for LocalStore.save_x25519_pub / get_x25519_pub / count_messages_after."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_x25519_pub_roundtrip(store):
    store.save_x25519_pub("did:key:alice", "AAABBBCCC=")
    assert store.get_x25519_pub("did:key:alice") == "AAABBBCCC="


def test_x25519_pub_missing_returns_none(store):
    assert store.get_x25519_pub("did:key:nobody") is None


def test_x25519_pub_insert_or_replace(store):
    store.save_x25519_pub("did:key:alice", "first_key=")
    store.save_x25519_pub("did:key:alice", "second_key=")
    assert store.get_x25519_pub("did:key:alice") == "second_key="


def test_count_messages_after_basic(store):
    base = time.time()
    # Save three messages at roughly base+0, base+1, base+2 seconds
    from datetime import datetime, timezone
    for i in range(3):
        ts = datetime.fromtimestamp(base + i, tz=timezone.utc).isoformat()
        store.save_message(f"msg-{i}", "thread-1", "relay",
                           "did:key:sender", None, f"content {i}", ts)

    # Counting after base-0.5 should see all 3
    assert store.count_messages_after("thread-1", base - 0.5) == 3
    # Counting after base+0.5 should see 2 (msg-1 and msg-2)
    assert store.count_messages_after("thread-1", base + 0.5) == 2
    # Counting after base+1.5 should see 1 (msg-2)
    assert store.count_messages_after("thread-1", base + 1.5) == 1
    # Counting after base+2.5 should see 0
    assert store.count_messages_after("thread-1", base + 2.5) == 0


def test_count_messages_after_different_threads(store):
    """count_messages_after is scoped to the given thread_id."""
    from datetime import datetime, timezone
    base = time.time()
    ts = datetime.fromtimestamp(base + 1, tz=timezone.utc).isoformat()
    store.save_message("msg-a", "thread-A", "relay", "did:key:s", None, "a", ts)
    store.save_message("msg-b", "thread-B", "relay", "did:key:s", None, "b", ts)

    # Both threads have one message after base
    assert store.count_messages_after("thread-A", base) == 1
    assert store.count_messages_after("thread-B", base) == 1
    # thread-A has no messages after base+1.5
    assert store.count_messages_after("thread-A", base + 1.5) == 0
    # thread-C is unknown — returns 0
    assert store.count_messages_after("thread-C", base) == 0
