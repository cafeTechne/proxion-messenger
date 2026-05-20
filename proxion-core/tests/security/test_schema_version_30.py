"""Tests for schema version 30 — thread integrity state, import provenance, webhook circuit columns."""
import pytest
import sqlite3

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "v30.db"))


class TestSchemaVersion30:
    def test_schema_version_bumped_to_30(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        conn.close()
        assert row[0] >= 35

    def test_thread_integrity_state_table_exists(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='thread_integrity_state'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_import_provenance_table_exists(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='import_provenance'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_webhook_delivery_logs_has_circuit_open_column(self, store):
        conn = sqlite3.connect(store.db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(webhook_delivery_logs)").fetchall()]
        conn.close()
        assert "circuit_open" in cols

    def test_webhook_delivery_logs_has_failure_streak_column(self, store):
        conn = sqlite3.connect(store.db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(webhook_delivery_logs)").fetchall()]
        conn.close()
        assert "failure_streak" in cols

    def test_thread_integrity_state_upsert_and_get(self, store):
        import time
        store.upsert_thread_integrity_state("thread-1", 5, "abc123", time.time())
        result = store.get_thread_integrity_state("thread-1")
        assert result is not None
        assert result["last_seq_num"] == 5
        assert result["last_prev_hash"] == "abc123"

    def test_thread_integrity_state_returns_none_for_missing(self, store):
        assert store.get_thread_integrity_state("nonexistent") is None

    def test_import_provenance_save_and_list(self, store):
        import time
        store.save_import_provenance(
            id="prov-1",
            source="backup-tool",
            body_sha256="deadbeef",
            imported_by="127.0.0.1",
            imported_at=time.time(),
            dry_run=False,
            summary_json='{"messages": 10}',
        )
        records = store.list_import_provenance()
        assert len(records) == 1
        assert records[0]["id"] == "prov-1"
        assert records[0]["source"] == "backup-tool"

    def test_import_provenance_dry_run_flag(self, store):
        import time
        store.save_import_provenance(
            id="prov-dry",
            source=None,
            body_sha256=None,
            imported_by=None,
            imported_at=time.time(),
            dry_run=True,
            summary_json='{}',
        )
        records = store.list_import_provenance()
        assert records[0]["dry_run"] == 1  # stored as integer

    def test_import_provenance_limit(self, store):
        import time
        for i in range(5):
            store.save_import_provenance(
                id=f"prov-{i}",
                source=None, body_sha256=None, imported_by=None,
                imported_at=time.time(), dry_run=False, summary_json='{}',
            )
        assert len(store.list_import_provenance(limit=3)) == 3
