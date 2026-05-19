"""Tests for the security program stability gate (R16)."""
import time
import pytest
from unittest.mock import patch
from proxion_messenger_core.security_exit_gates import evaluate_security_program_stability_gate


@pytest.fixture
def store(tmp_path):
    from proxion_messenger_core.local_store import LocalStore
    return LocalStore(str(tmp_path / "test.db"))


def _all_pass_gates():
    return {"all_pass": True, "gates": {}, "evaluated_at": time.time()}


def test_gate_fails_when_exit_gates_fail(store):
    # Fresh store has no drills → drill gate fails → stability gate fails
    result = evaluate_security_program_stability_gate(store, stability_days=45)
    assert result["pass"] is False
    assert result["recommendation"] == "continue_hardening"


def test_fails_with_recent_critical_events(store):
    store.save_security_event("authn_bypass_confirmed", "critical", details="simulated")

    with patch(
        "proxion_messenger_core.security_exit_gates.evaluate_all_gates",
        return_value=_all_pass_gates(),
    ):
        result = evaluate_security_program_stability_gate(store, stability_days=45)

    assert result["pass"] is False
    assert result["critical_event_count_window"] >= 1
    assert result["recommendation"] == "continue_hardening"


def test_recommends_hold_line_when_passed(store):
    with patch(
        "proxion_messenger_core.security_exit_gates.evaluate_all_gates",
        return_value=_all_pass_gates(),
    ):
        result = evaluate_security_program_stability_gate(store, stability_days=45)

    assert result["pass"] is True
    assert result["recommendation"] == "hold_line"
    assert result["days_stable"] == 45.0
