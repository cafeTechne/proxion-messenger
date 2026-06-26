"""Tests for security drill evidence persistence (R15)."""
import tempfile
import time
import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.security_exit_gates import evaluate_drill_gate


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    return LocalStore(path)


class TestDrillResultsPersistAndQuery:
    def test_drill_results_persist_and_query_correctly(self, store):
        store.save_drill_result(
            drill_id="drill-persist-001",
            drill_type="incident",
            status="pass",
            findings={"steps_completed": 5, "blockers": 0},
            duration_seconds=2400,
        )
        drills = store.get_drill_results_in_window(time.time() - 60, time.time() + 1)
        assert any(d["drill_id"] == "drill-persist-001" for d in drills)
        d = next(x for x in drills if x["drill_id"] == "drill-persist-001")
        assert d["drill_type"] == "incident"
        assert d["status"] == "pass"
        assert d["duration_seconds"] == 2400

    def test_drill_gate_fails_without_recent_pass(self, store):
        store.save_drill_result("old-fail", "incident", "fail", {}, 3600)
        result = evaluate_drill_gate(store, window_days=30)
        assert result["pass"] is False

    def test_drill_gate_passes_with_recent_incident_and_recovery_pass(self, store):
        store.save_drill_result("inc-pass", "incident", "pass", {"notes": "clean run"}, 900)
        store.save_drill_result("rec-pass", "recovery", "pass", {"notes": "under budget"}, 1800)
        result = evaluate_drill_gate(store, window_days=30)
        assert result["pass"] is True

    def test_multiple_drill_types_stored_independently(self, store):
        store.save_drill_result("d-inc", "incident", "pass", {}, 600)
        store.save_drill_result("d-rec", "recovery", "fail", {}, 4000)
        store.save_drill_result("d-rb", "rollback", "pass", {}, 300)
        drills = store.get_drill_results_in_window(time.time() - 60, time.time() + 1)
        types = {d["drill_type"] for d in drills}
        assert "incident" in types
        assert "recovery" in types
        assert "rollback" in types
