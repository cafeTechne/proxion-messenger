"""Round 4: Retention purge for audit logs and security events."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "ret.db"))


def test_audit_logs_purged_by_retention_window(store):
    """purge_old_audit_logs removes entries older than cutoff."""
    store.save_audit_log_chained("old_event", "info")
    before = time.time()
    count = store.purge_old_audit_logs(before + 1)  # cutoff in the future
    assert count >= 1, f"Should have purged at least 1, got {count}"
    assert not store.get_audit_logs(), "No entries should remain after purge"


def test_security_events_purged_by_retention_window(store):
    """purge_old_security_events removes entries older than cutoff."""
    store.save_security_event("test_event", "info", details="test")
    before = time.time()
    count = store.purge_old_security_events(before + 1)
    assert count >= 1, f"Should have purged at least 1, got {count}"
    assert not store.get_security_events(), "No events should remain after purge"


def test_purge_metrics_increment_after_cleanup(store):
    """Purge methods return counts that can be tracked as metrics."""
    store.save_audit_log_chained("event1", "info")
    store.save_audit_log_chained("event2", "warning")
    count = store.purge_old_audit_logs(time.time() + 1)
    assert count == 2, f"Expected 2 purged, got {count}"
    # Fresh event added after purge should survive a cutoff before it
    store.save_audit_log_chained("event3", "info")
    count2 = store.purge_old_audit_logs(time.time() - 1)  # cutoff in the past
    assert count2 == 0, "Fresh entry should not be purged"
