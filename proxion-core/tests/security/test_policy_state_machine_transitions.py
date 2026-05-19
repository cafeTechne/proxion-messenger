"""Tests for policy tier state machine (R15)."""
import tempfile
import time
import pytest

from proxion_messenger_core.policy_state_machine import (
    PolicyStateMachine,
    IllegalTierTransition,
    TIER_NORMAL,
    TIER_ELEVATED,
    TIER_RESTRICTIVE,
    TIER_CONTAINMENT,
)


@pytest.fixture
def machine():
    return PolicyStateMachine(cooldown_s=0)


@pytest.fixture
def machine_with_cooldown():
    return PolicyStateMachine(cooldown_s=300)


class TestIllegalTierTransition:
    def test_illegal_tier_transition_rejected(self, machine_with_cooldown):
        machine_with_cooldown.transition(TIER_CONTAINMENT, trigger_type="auto")
        with pytest.raises(IllegalTierTransition):
            machine_with_cooldown.transition(TIER_NORMAL, trigger_type="manual")

    def test_invalid_tier_number_raises(self, machine):
        with pytest.raises(IllegalTierTransition):
            machine.transition(99)

    def test_upward_transition_always_allowed(self, machine_with_cooldown):
        for tier in (TIER_ELEVATED, TIER_RESTRICTIVE, TIER_CONTAINMENT):
            machine_with_cooldown.transition(tier, trigger_type="auto")
        assert machine_with_cooldown.current_tier() == TIER_CONTAINMENT


class TestCooldownPreventsPrematureDeescalation:
    def test_cooldown_prevents_premature_deescalation(self):
        machine = PolicyStateMachine(cooldown_s=3600)
        machine.transition(TIER_RESTRICTIVE, trigger_type="drift")
        with pytest.raises(IllegalTierTransition) as exc_info:
            machine.transition(TIER_NORMAL, trigger_type="manual")
        assert "cooldown" in str(exc_info.value).lower()

    def test_deescalation_allowed_after_cooldown(self):
        machine = PolicyStateMachine(cooldown_s=0)
        machine.transition(TIER_RESTRICTIVE, trigger_type="auto")
        record = machine.transition(TIER_NORMAL, trigger_type="manual")
        assert record["to_tier"] == "normal"
        assert machine.current_tier() == TIER_NORMAL

    def test_can_deescalate_returns_false_during_cooldown(self):
        machine = PolicyStateMachine(cooldown_s=3600)
        machine.transition(TIER_ELEVATED, trigger_type="auto")
        assert machine.can_deescalate() is False


class TestTransitionLedgerEntry:
    def test_transition_ledger_entry_written(self, machine):
        machine.transition(TIER_ELEVATED, trigger_type="auto", trigger_detail="auth_lockouts=4")
        transitions = machine.recent_transitions()
        assert len(transitions) >= 1
        t = transitions[-1]
        assert t["from_tier"] == "normal"
        assert t["to_tier"] == "elevated"
        assert t["trigger_type"] == "auto"
        assert t["trigger_detail"] == "auth_lockouts=4"

    def test_transition_record_has_id_and_timestamp(self, machine):
        record = machine.transition(TIER_ELEVATED, trigger_type="drift")
        assert "id" in record
        assert "created_at" in record
        assert record["id"] != ""
