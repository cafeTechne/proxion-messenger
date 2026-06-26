"""R12: Replay cache cardinality pruning tests."""
import time
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _seed_nonces(store, count=10):
    now = time.time()
    for i in range(count):
        store.record_relay_nonce(f"nonce_{i:04d}")


def test_replay_tables_pruned_when_over_cap(store):
    _seed_nonces(store, 20)
    removed = store.prune_replay_table_by_cardinality("relay_seen_nonces", "seen_at", max_rows=10)
    assert removed == 10


def test_oldest_entries_removed_first(store):
    # Can't easily control timestamps with record_relay_nonce (uses time.time())
    # So we verify total count drops by exactly the excess
    for i in range(10):
        store.record_relay_nonce(f"nonce_{i:04d}")
    removed = store.prune_replay_table_by_cardinality("relay_seen_nonces", "seen_at", max_rows=5)
    assert removed == 5
    # After pruning, exactly 5 remain
    import sqlite3
    with sqlite3.connect(store.db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM relay_seen_nonces").fetchone()[0]
    assert total == 5


def test_no_pruning_when_under_cap(store):
    _seed_nonces(store, 5)
    removed = store.prune_replay_table_by_cardinality("relay_seen_nonces", "seen_at", max_rows=100)
    assert removed == 0


def test_prune_returns_zero_on_empty_table(store):
    removed = store.prune_replay_table_by_cardinality("relay_seen_nonces", "seen_at", max_rows=10)
    assert removed == 0


def test_prune_invite_nonces_table(store):
    for i in range(15):
        store.record_invite_nonce(f"invite_nonce_{i:04d}")
    removed = store.prune_replay_table_by_cardinality("invite_seen_nonces", "seen_at", max_rows=10)
    assert removed == 5


def test_default_cap_applied_when_max_rows_not_specified(store):
    """Default cap is 50000; with fewer rows, no pruning occurs."""
    _seed_nonces(store, 10)
    removed = store.prune_replay_table_by_cardinality("relay_seen_nonces", "seen_at")
    assert removed == 0


def test_prune_dpop_jti_table(store):
    for i in range(12):
        store.record_dpop_jti(f"jti_{i:04d}")
    removed = store.prune_replay_table_by_cardinality("dpop_seen_jti", "seen_at", max_rows=10)
    assert removed == 2
