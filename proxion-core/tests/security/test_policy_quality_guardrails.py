"""Tests for policy quality guardrails (R16)."""
import pytest
from proxion_messenger_core.policy_quality import PolicyQualityMonitor


@pytest.fixture
def monitor():
    return PolicyQualityMonitor()


def test_churn_triggers_guard(monitor):
    for i in range(10):
        monitor.record_transition(from_tier=0, to_tier=1)
    result = monitor.evaluate_quality()
    assert result["frozen"] is True
    assert result["churn_count"] >= 10


def test_guard_freezes_auto_escalation(monitor):
    from proxion_messenger_core.security_policy import SecurityPolicy, TIER_NORMAL

    pol = SecurityPolicy()
    assert pol.get_tier() == TIER_NORMAL

    for _ in range(10):
        monitor.record_transition(0, 1)

    from unittest.mock import patch
    with patch("proxion_messenger_core.policy_quality.get_quality_monitor", return_value=monitor):
        signals = {
            "auth_lockouts": 20,
            "schema_rejects": 5,
            "replay_rejects": 0,
            "db_integrity_events": 0,
            "policy_deny_events": 0,
        }
        new_tier = pol.escalate_tier_from_signals(signals)

    # Frozen monitor blocks escalation — tier stays at NORMAL
    assert new_tier == TIER_NORMAL


def test_guard_emits_security_event(tmp_path):
    from proxion_messenger_core.local_store import LocalStore
    store = LocalStore(str(tmp_path / "test.db"))

    monitor = PolicyQualityMonitor()
    for _ in range(10):
        monitor.record_transition(0, 1)
    monitor.evaluate_quality(store=store)

    events = store.get_security_events(limit=10)
    types = [e["event_type"] for e in events]
    assert "policy_quality_guard_triggered" in types
