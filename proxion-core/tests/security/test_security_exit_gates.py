"""Tests for security exit gate evaluators (R15)."""
import tempfile
import time
import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.security_exit_gates import (
    evaluate_all_gates,
    evaluate_risk_register_gate,
    evaluate_control_baseline_gate,
    evaluate_slo_gate,
    evaluate_drill_gate,
    evaluate_false_positive_gate,
)


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    return LocalStore(path)


class TestExitGateEvaluators:
    def test_exit_gate_passes_when_all_conditions_satisfied(self, store):
        store.save_drill_result("d1", "incident", "pass", {}, 900)
        store.save_drill_result("d2", "recovery", "pass", {}, 1800)
        result = evaluate_all_gates(store)
        assert isinstance(result["all_pass"], bool)
        assert "gates" in result
        assert "evaluated_at" in result

    def test_exit_gate_fails_with_unresolved_high_risk_items(self, store):
        store.save_security_event("authn_bypass_confirmed", "critical", details="test")
        result = evaluate_risk_register_gate(store)
        assert result["pass"] is False
        assert "unresolved" in result["reason"]

    def test_exit_gate_command_owner_only(self):
        from proxion_messenger_core.security_policy import _OWNER_ONLY_COMMANDS
        assert "get_security_exit_gate_status" in _OWNER_ONLY_COMMANDS

    def test_control_baseline_gate_returns_dict(self, store):
        result = evaluate_control_baseline_gate(store)
        assert "pass" in result
        assert "detail" in result
        assert isinstance(result["detail"], dict)

    def test_slo_gate_returns_pass_when_no_snapshots(self, store):
        result = evaluate_slo_gate(store, window_days=30)
        assert result["pass"] is True

    def test_drill_gate_fails_without_recent_drills(self, store):
        result = evaluate_drill_gate(store, window_days=30)
        assert result["pass"] is False
        assert "drill_requirements_not_met" in result["reason"]

    def test_drill_gate_passes_with_incident_and_recovery(self, store):
        store.save_drill_result("di1", "incident", "pass", {}, 600)
        store.save_drill_result("dr1", "recovery", "pass", {}, 1200)
        result = evaluate_drill_gate(store, window_days=30)
        assert result["pass"] is True

    def test_false_positive_gate_passes_with_no_events(self, store):
        result = evaluate_false_positive_gate(store, window_days=30)
        assert result["pass"] is True
