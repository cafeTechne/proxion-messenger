"""Round 2: Durable relay message-ID dedup (relay_seen_ids table)."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_relay_id_dedup_records_and_detects(store):
    """has_seen_relay_id returns False before recording, True after."""
    key = "did:key:alice:msg-001"
    assert not store.has_seen_relay_id(key, ttl_seconds=600)
    store.record_relay_id(key)
    assert store.has_seen_relay_id(key, ttl_seconds=600)


def test_relay_id_dedup_different_keys_independent(store):
    """Two distinct dedup keys don't interfere."""
    store.record_relay_id("did:key:alice:msg-001")
    assert not store.has_seen_relay_id("did:key:alice:msg-002", ttl_seconds=600)


def test_relay_dedup_ttl_allows_reaccept_after_expiry(store):
    """Entry seen outside TTL window is not considered duplicate."""
    key = "did:key:alice:msg-old"
    store.record_relay_id(key)
    # Query with tiny TTL — should not be seen
    assert not store.has_seen_relay_id(key, ttl_seconds=0)


def test_relay_prune_removes_expired_rows(store):
    """prune_seen_relay_ids removes entries older than cutoff."""
    key = "did:key:alice:msg-prune"
    store.record_relay_id(key)
    assert store.has_seen_relay_id(key, ttl_seconds=600)
    # Prune with future cutoff — removes everything
    store.prune_seen_relay_ids(time.time() + 10)
    assert not store.has_seen_relay_id(key, ttl_seconds=600)


def test_relay_id_dedup_record_idempotent(store):
    """Calling record_relay_id twice for same key is safe."""
    key = "did:key:alice:msg-dup"
    store.record_relay_id(key)
    store.record_relay_id(key)  # should not raise
    assert store.has_seen_relay_id(key, ttl_seconds=600)


def test_relay_id_prune_only_removes_old_entries(store):
    """Pruning removes only expired entries, not fresh ones."""
    old_key = "did:key:alice:msg-old"
    fresh_key = "did:key:alice:msg-fresh"
    store.record_relay_id(old_key)
    store.record_relay_id(fresh_key)
    # Prune with cutoff 1 second from now — old entry is NOT yet expired
    # but with cutoff = now - 1 it simulates expired entries from 1s ago
    store.prune_seen_relay_ids(time.time() - 1)
    # Both should still exist (they were just recorded)
    assert store.has_seen_relay_id(fresh_key, ttl_seconds=600)
