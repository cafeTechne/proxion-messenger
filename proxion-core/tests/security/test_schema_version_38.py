"""Tests for schema version 38 migration (R15)."""
import json
import sqlite3
import tempfile
import time
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    return LocalStore(path)


class TestSchemaVersion38:
    def test_schema_version_bumped_to_38(self):
        assert LocalStore._SCHEMA_VERSION >= 38

    def test_security_slo_snapshots_table_exists(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='security_slo_snapshots'"
        ).fetchone()
        conn.close()
        assert row is not None, "security_slo_snapshots table must exist"

    def test_security_drill_results_table_exists(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='security_drill_results'"
        ).fetchone()
        conn.close()
        assert row is not None, "security_drill_results table must exist"

    def test_slo_snapshot_crud(self, store):
        now = time.time()
        store.save_slo_snapshot(
            snapshot_id="snap-001",
            window_start=now - 86400 * 30,
            window_end=now,
            metrics={"relay_replay_false_negatives": 0, "authn_bypass_incidents": 0},
            evaluated_at=now,
        )
        snaps = store.get_slo_snapshots_in_window(now - 86400 * 31, now + 1)
        assert len(snaps) >= 1
        assert snaps[0]["id"] == "snap-001"

    def test_drill_result_crud(self, store):
        store.save_drill_result(
            drill_id="drill-001",
            drill_type="recovery",
            status="pass",
            findings={"mttr_seconds": 1800, "issues_found": 0},
            duration_seconds=1800,
        )
        drills = store.get_drill_results_in_window(time.time() - 100, time.time() + 1)
        assert len(drills) >= 1
        assert drills[0]["drill_id"] == "drill-001"
        assert drills[0]["status"] == "pass"

    def test_drill_results_index_exists(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_security_drill_results_time'"
        ).fetchone()
        conn.close()
        assert row is not None
