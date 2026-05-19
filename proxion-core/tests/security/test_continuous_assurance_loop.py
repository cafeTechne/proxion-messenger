"""Tests for the continuous assurance loop (R16)."""
import pytest
from unittest.mock import patch
from proxion_messenger_core.continuous_assurance import (
    ContinuousAssuranceLoop,
    run_assurance_evaluation,
    ASSURANCE_GREEN,
    ASSURANCE_AMBER,
    ASSURANCE_RED,
)


@pytest.fixture
def store(tmp_path):
    from proxion_messenger_core.local_store import LocalStore
    return LocalStore(str(tmp_path / "test.db"))


def test_run_once_returns_valid_assurance_state(store):
    loop = ContinuousAssuranceLoop(store=store)
    result = loop.run_once()
    assert "assurance_state" in result
    assert result["assurance_state"] in (ASSURANCE_GREEN, ASSURANCE_AMBER, ASSURANCE_RED)


def test_degraded_after_repeated_evaluation_failures(store):
    loop = ContinuousAssuranceLoop(store=store)
    assert not loop.is_degraded()

    with patch(
        "proxion_messenger_core.continuous_assurance.run_assurance_evaluation",
        side_effect=RuntimeError("eval_failed"),
    ):
        for _ in range(3):
            try:
                loop.run_once()
            except RuntimeError:
                pass

    assert loop.is_degraded()
    events = store.get_security_events(event_type="assurance_loop_degraded", limit=5)
    assert len(events) >= 1


def test_assurance_evaluation_contains_gate_results(store):
    result = run_assurance_evaluation(store=store)
    assert "assurance_state" in result
    assert "gates" in result
    assert "checks" in result
    assert result["assurance_state"] in (ASSURANCE_GREEN, ASSURANCE_AMBER, ASSURANCE_RED)
