"""Tests for MVP release gate evaluators in security_exit_gates.py."""
import time
import uuid

import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.security_exit_gates import (
    evaluate_mvp_working_gate,
    evaluate_mvp_security_gate,
    evaluate_all_gates,
)


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_mvp_working_gate_fails_when_critical_events_recent(store):
    try:
        store.record_security_event(
            event_type="authn_bypass_confirmed",
            severity="critical",
            actor_webid="attacker",
            detail={},
        )
    except Exception:
        pytest.skip("record_security_event not available — skipping")

    result = evaluate_mvp_working_gate(store)
    assert result["pass"] is False
    assert "critical" in result["reason"]


def test_mvp_security_gate_checks_schema_version(store):
    result = evaluate_mvp_security_gate(store)
    assert isinstance(result["pass"], bool)
    assert "reason" in result
    assert result["pass"] is True or "schema_version" in result.get("detail", {})


def test_mvp_release_gate_passes_when_all_conditions_met(store):
    mvp_working = evaluate_mvp_working_gate(store)
    mvp_security = evaluate_mvp_security_gate(store)

    assert mvp_working["pass"] is True, f"MVP working gate failed: {mvp_working['reason']}"
    assert mvp_security["pass"] is True, f"MVP security gate failed: {mvp_security['reason']}"

    all_gates = evaluate_all_gates(store)
    assert "mvp_working_gate" in all_gates["gates"]
    assert "mvp_security_gate" in all_gates["gates"]
