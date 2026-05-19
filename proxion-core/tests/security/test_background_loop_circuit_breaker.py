"""Tests for background loop health event emission (Round 5)."""
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


class TestBackgroundLoopCircuitBreaker:
    def test_background_degraded_event_is_recorded(self, store):
        store.save_background_health_event("scheduler_loop", 10, "some error")
        events = store.get_security_events(event_type="background_loop_degraded")
        assert len(events) == 1
        assert "scheduler_loop" in events[0]["details"]

    def test_background_health_uses_security_events_table(self, store):
        store.save_background_health_event("expire_messages_loop", 5, "timeout")
        all_events = store.get_security_events()
        loop_events = [e for e in all_events if e["event_type"] == "background_loop_degraded"]
        assert len(loop_events) == 1

    def test_background_degraded_event_has_warning_severity(self, store):
        store.save_background_health_event("relay_retry_loop", 10)
        events = store.get_security_events(event_type="background_loop_degraded")
        assert events[0]["severity"] == "warning"
