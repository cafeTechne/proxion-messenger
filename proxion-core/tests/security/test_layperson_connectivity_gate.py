"""Tests for layperson_connectivity_gate evaluator."""
import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.wg_overlay import WgOverlayManager
from proxion_messenger_core.security_exit_gates import (
    evaluate_layperson_connectivity_gate,
    evaluate_all_gates,
)


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_gate_fails_when_schema_below_48(store):
    original = store._SCHEMA_VERSION
    try:
        store.__class__._SCHEMA_VERSION = 47
        result = evaluate_layperson_connectivity_gate(store)
        assert result["pass"] is False
        assert "schema_version" in result["reason"] or "schema" in result["reason"]
    finally:
        store.__class__._SCHEMA_VERSION = original


def test_gate_fails_when_no_wg_identity(store):
    assert store._SCHEMA_VERSION >= 48
    assert store.get_wg_local_identity() is None

    result = evaluate_layperson_connectivity_gate(store)
    assert result["pass"] is False
    assert "wg_identity" in result["reason"]


def test_gate_passes_when_schema_current_and_identity_present(store):
    manager = WgOverlayManager(store)
    manager.ensure_local_identity()

    result = evaluate_layperson_connectivity_gate(store)
    assert result["pass"] is True
    assert result["reason"] == "overlay_identity_present"

    all_gates = evaluate_all_gates(store)
    assert "layperson_connectivity_gate" in all_gates["gates"]
