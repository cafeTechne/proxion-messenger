"""Tests for security SLO evaluation (R15)."""
import json
import tempfile
import time
import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.security_exit_gates import evaluate_slo_gate, evaluate_false_positive_gate


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    return LocalStore(path)


class TestSloSnapshotRecordsMetrics:
    def test_slo_snapshot_records_metrics_for_window(self, store):
        now = time.time()
        store.save_slo_snapshot(
            snapshot_id="slo-test-001",
            window_start=now - 86400 * 30,
            window_end=now,
            metrics={"relay_replay_false_negatives": 0, "authn_bypass_incidents": 0},
        )
        snaps = store.get_slo_snapshots_in_window(now - 86400 * 31, now + 1)
        assert any(s["id"] == "slo-test-001" for s in snaps)
        snap = next(s for s in snaps if s["id"] == "slo-test-001")
        metrics = json.loads(snap["metrics_json"])
        assert metrics["relay_replay_false_negatives"] == 0

    def test_slo_gate_respects_30_day_window(self, store):
        now = time.time()
        old_time = now - 86400 * 35
        store.save_slo_snapshot(
            snapshot_id="slo-old",
            window_start=old_time - 86400 * 30,
            window_end=old_time,
            metrics={"violation": True},
        )
        result = evaluate_slo_gate(store, window_days=30)
        assert result["pass"] is True

    def test_multiple_snapshots_returned_in_window(self, store):
        now = time.time()
        for i in range(3):
            store.save_slo_snapshot(
                snapshot_id=f"slo-multi-{i}",
                window_start=now - 86400 * (30 - i),
                window_end=now - 86400 * (29 - i),
                metrics={"relay_replay_false_negatives": 0},
                evaluated_at=now - 86400 * (29 - i),
            )
        snaps = store.get_slo_snapshots_in_window(now - 86400 * 31, now + 1)
        assert len(snaps) >= 3


class TestFalsePositiveGate:
    def test_false_positive_gate_uses_containment_stats(self, store):
        for _ in range(200):
            store.save_security_event("containment_activated", "warning")
        for _ in range(1):
            store.save_security_event("containment_false_positive", "info")
        result = evaluate_false_positive_gate(store, window_days=30)
        assert result["pass"] is True

    def test_false_positive_gate_fails_when_rate_exceeded(self, store):
        for _ in range(10):
            store.save_security_event("containment_activated", "warning")
        for _ in range(5):
            store.save_security_event("containment_false_positive", "info")
        result = evaluate_false_positive_gate(store, window_days=30)
        assert result["pass"] is False
        assert result["detail"]["rate"] >= 0.01
