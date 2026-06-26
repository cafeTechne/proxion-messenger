"""R10: Adaptive security tier tests."""
import time
import pytest

from proxion_messenger_core.security_policy import (
    SecurityPolicy, TIER_NORMAL, TIER_ELEVATED, TIER_RESTRICTIVE, TIER_CONTAINMENT,
    reload_policy,
)


@pytest.fixture(autouse=True)
def reset_policy():
    reload_policy()
    yield
    reload_policy()


def _make_policy():
    return SecurityPolicy()


def test_default_tier_is_normal():
    policy = _make_policy()
    assert policy.get_tier() == TIER_NORMAL


def test_escalation_to_tier1_on_abuse_threshold():
    policy = _make_policy()
    signals = {"auth_lockouts": 3, "schema_rejects": 0, "replay_rejects": 0, "db_integrity_events": 0}
    new_tier = policy.escalate_tier_from_signals(signals)
    assert new_tier >= TIER_ELEVATED


def test_tier2_blocks_high_risk_mutations():
    policy = _make_policy()
    policy.set_tier(TIER_RESTRICTIVE, reason="test")
    decision = policy.evaluate_ws_command(
        cmd="prepare_recovery_operation",
        caller_webid="did:key:owner",
        gateway_owner_did="did:key:owner",
    )
    assert not decision.allow
    assert decision.deny_code == "E_RESTRICTED"


def test_tier3_blocks_additional_commands():
    policy = _make_policy()
    policy.set_tier(TIER_CONTAINMENT, reason="test")
    for cmd in ("connect_css", "prepare_recovery_operation"):
        decision = policy.evaluate_ws_command(
            cmd=cmd,
            caller_webid="did:key:owner",
            gateway_owner_did="did:key:owner",
        )
        assert not decision.allow
        assert decision.deny_code == "E_CONTAINMENT"


def test_manual_tier_override_with_ttl_expires_correctly():
    policy = _make_policy()
    policy.set_tier(TIER_RESTRICTIVE, override_ttl_s=0.01, reason="test")
    assert policy.get_tier() == TIER_RESTRICTIVE
    time.sleep(0.05)  # let TTL expire
    assert policy.get_tier() == TIER_NORMAL


def test_tier1_returns_rate_multiplier_half():
    policy = _make_policy()
    policy.set_tier(TIER_ELEVATED)
    assert policy.get_rate_multiplier() == 0.5


def test_tier0_returns_rate_multiplier_one():
    policy = _make_policy()
    assert policy.get_rate_multiplier() == 1.0


def test_tier3_containment_on_db_integrity():
    policy = _make_policy()
    signals = {"db_integrity_events": 1, "auth_lockouts": 0, "schema_rejects": 0, "replay_rejects": 0}
    new_tier = policy.escalate_tier_from_signals(signals)
    assert new_tier == TIER_CONTAINMENT


def test_tier2_on_high_replay_rejects():
    policy = _make_policy()
    signals = {"db_integrity_events": 0, "auth_lockouts": 0, "schema_rejects": 0, "replay_rejects": 50}
    new_tier = policy.escalate_tier_from_signals(signals)
    assert new_tier >= TIER_RESTRICTIVE


def test_get_tier_state_returns_all_fields():
    policy = _make_policy()
    policy.set_tier(TIER_ELEVATED, reason="probe")
    state = policy.get_tier_state()
    assert "tier" in state
    assert "tier_name" in state
    assert "reasons" in state
    assert state["tier"] == TIER_ELEVATED
    assert state["tier_name"] == "elevated"


def test_tier_does_not_downgrade_automatically():
    policy = _make_policy()
    policy.escalate_tier_from_signals({"auth_lockouts": 8, "db_integrity_events": 0,
                                        "schema_rejects": 0, "replay_rejects": 0})
    current = policy.get_tier()
    # Lower-signal call should not downgrade
    policy.escalate_tier_from_signals({"auth_lockouts": 0, "db_integrity_events": 0,
                                        "schema_rejects": 0, "replay_rejects": 0})
    assert policy.get_tier() == current
