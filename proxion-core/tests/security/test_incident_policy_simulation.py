"""R11: Incident policy simulation tests."""
import time
import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.incident_sim import simulate_incident_policy
from proxion_messenger_core.security_policy import SecurityPolicy


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_simulation_runs_on_recent_security_events(store):
    for i in range(5):
        store.save_security_event("auth_failed", "warning", details=f"attempt {i}")
    report = simulate_incident_policy(store, hours=1)
    assert "events_replayed" in report
    assert report["events_replayed"] >= 5


def test_simulation_reports_blocked_actions_and_candidates(store):
    report = simulate_incident_policy(store, hours=24)
    assert "blocked_actions" in report
    assert "false_positive_candidates" in report
    assert isinstance(report["false_positive_candidates"], list)


def test_simulation_returns_escalation_timeline(store):
    # Insert enough events to trigger tier escalation
    for i in range(15):
        store.save_security_event("auth_lockout", "critical", details=f"lockout {i}")
    report = simulate_incident_policy(store, hours=24, tier_profile={"tier1_auth_lockouts": 1})
    assert "escalation_timeline" in report
    assert "final_tier" in report
    assert report["final_tier"] >= 1


def test_simulation_owner_only_access():
    """simulate_incident_policy must be in the owner-only command set."""
    from proxion_messenger_core.security_policy import SecurityPolicy
    policy = SecurityPolicy()
    decision = policy.evaluate_ws_command(
        cmd="simulate_incident_policy",
        caller_webid="did:key:non_owner",
        gateway_owner_did="did:key:owner",
    )
    assert not decision.allow
    assert decision.deny_code == "E_FORBIDDEN"


def test_simulation_hours_clamped_to_maximum(store):
    report = simulate_incident_policy(store, hours=99999)
    assert report["hours"] == 168


def test_simulation_empty_store_returns_zero_events(store):
    report = simulate_incident_policy(store, hours=1)
    assert report["events_replayed"] == 0
    assert report["final_tier"] == 0
