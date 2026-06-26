"""Round 3: Relay queue expiry and max-attempt enforcement."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_enqueue_sets_relay_expiry(store):
    """enqueue_relay sets expires_at to now + 86400."""
    before = time.time()
    store.enqueue_relay("relay-001", "did:key:bob", "wss://bob.example.com", {"msg": "hi"})
    after = time.time()
    rows = store._conn().execute("SELECT expires_at FROM pending_relays WHERE id='relay-001'").fetchall()
    assert rows, "Row should exist"
    expires_at = rows[0][0]
    assert before + 86400 - 5 <= expires_at <= after + 86400 + 5


def test_retry_loop_skips_expired_relays(store):
    """get_pending_relays excludes rows whose expires_at is in the past."""
    store.enqueue_relay("relay-expired", "did:key:bob", "wss://bob.example.com", {"msg": "old"})
    # Force expires_at to the past
    with store._conn() as conn:
        conn.execute(
            "UPDATE pending_relays SET expires_at = ? WHERE id = ?",
            (time.time() - 10, "relay-expired"),
        )
    pending = store.get_pending_relays()
    ids = [r["id"] for r in pending]
    assert "relay-expired" not in ids, "Expired relay should not be returned"


def test_relay_marked_failed_after_max_attempts(store):
    """get_pending_relays excludes rows with attempt_count >= 10."""
    store.enqueue_relay("relay-maxed", "did:key:bob", "wss://bob.example.com", {"msg": "retry"})
    # Set attempt_count to 10
    with store._conn() as conn:
        conn.execute(
            "UPDATE pending_relays SET attempt_count = 10 WHERE id = ?",
            ("relay-maxed",),
        )
    pending = store.get_pending_relays()
    ids = [r["id"] for r in pending]
    assert "relay-maxed" not in ids, "Max-attempt relay should not be returned"
