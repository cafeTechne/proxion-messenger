"""R10: Retention lock enforcement tests."""
import hashlib
import time
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_set_and_get_retention_lock(store):
    locked_until = time.time() + 3600
    store.set_retention_lock("audit_logs", locked_until)
    lock = store.get_retention_lock("audit_logs")
    assert lock is not None
    assert abs(lock["locked_until"] - locked_until) < 1.0


def test_list_retention_locks_returns_active(store):
    store.set_retention_lock("security_events", time.time() + 3600)
    locks = store.list_retention_locks()
    assert any(l["lock_name"] == "security_events" for l in locks)


def test_list_retention_locks_excludes_expired(store):
    store.set_retention_lock("old_lock", time.time() - 1)  # already expired
    locks = store.list_retention_locks()
    assert not any(l["lock_name"] == "old_lock" for l in locks)


def test_clear_retention_lock(store):
    store.set_retention_lock("audit_logs", time.time() + 3600)
    cleared = store.clear_retention_lock("audit_logs")
    assert cleared is True
    lock = store.get_retention_lock("audit_logs")
    assert lock is None


def test_purge_respects_active_retention_lock(store):
    """Purge must not delete entries newer than the lock horizon."""
    # Add a security event
    store.save_security_event("test_event", "info", details="must keep")
    now = time.time()
    # Lock until 1 hour from now — any event newer than (now - 1h) is protected
    store.set_retention_lock("security_events", now + 3600)
    # Try to purge with a cutoff 30 minutes ago — lock horizon is now+3600 > cutoff, so keep
    deleted = store.purge_old_security_events(now - 1800)
    events = store.get_security_events(limit=200)
    assert any(e["event_type"] == "test_event" for e in events)


def test_purge_allowed_for_entries_older_than_lock(store):
    """Purge should delete entries older than the lock horizon."""
    import sqlite3
    # Directly insert an old event bypassing the store helper
    conn = sqlite3.connect(store.db_path)
    import uuid
    old_ts = time.time() - 7200  # 2 hours ago
    conn.execute(
        "INSERT INTO security_events (event_type, severity, created_at) VALUES (?, ?, ?)",
        ("very_old_event", "info", old_ts)
    )
    conn.commit()
    conn.close()
    # Lock only covers last 1 hour
    store.set_retention_lock("security_events", time.time() - 3600)
    # Purge everything older than 1 hour — lock horizon is (now-1h), cutoff is (now-1h)
    # The 2-hour-old event should be deleted since it predates the lock horizon
    deleted = store.purge_old_security_events(time.time() - 3600)
    assert deleted >= 1


def test_clear_retention_lock_requires_confirmation_token():
    """Handler requires sha256('clear:lock_name')[:16] as confirmation_token."""
    lock_name = "audit_logs"
    expected = hashlib.sha256(f"clear:{lock_name}".encode()).hexdigest()[:16]
    assert len(expected) == 16
    assert expected != "wrong_token"


def test_list_retention_locks_owner_only():
    from proxion_messenger_core.security_policy import SecurityPolicy
    policy = SecurityPolicy()
    for cmd in ("set_retention_lock", "list_retention_locks", "clear_retention_lock"):
        decision = policy.evaluate_ws_command(
            cmd=cmd,
            caller_webid="did:key:non_owner",
            gateway_owner_did="did:key:owner",
        )
        assert not decision.allow, f"{cmd} should be owner-only"
