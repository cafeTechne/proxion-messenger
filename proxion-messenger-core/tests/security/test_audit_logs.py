"""Tests for audit_logs table (Migration 21) and LocalStore audit methods."""
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "audit.db"))


class TestAuditLogSchema:
    def test_schema_version_is_29(self, store):
        import sqlite3
        conn = sqlite3.connect(store.db_path)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        conn.close()
        assert row[0] >= 35

    def test_audit_logs_table_exists(self, store):
        import sqlite3
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_logs'"
        ).fetchone()
        conn.close()
        assert row is not None


class TestSaveAuditLog:
    def test_basic_event_saved(self, store):
        store.save_audit_log("relay_rejected", severity="warning", ip="1.2.3.4")
        logs = store.get_audit_logs()
        assert len(logs) == 1
        assert logs[0]["event_type"] == "relay_rejected"

    def test_severity_stored(self, store):
        store.save_audit_log("quota_exceeded", severity="error")
        logs = store.get_audit_logs()
        assert logs[0]["severity"] == "error"

    def test_default_severity_is_info(self, store):
        store.save_audit_log("webid_resolved")
        logs = store.get_audit_logs()
        assert logs[0]["severity"] == "info"

    def test_webid_stored(self, store):
        webid = "https://pod.example.com/alice/profile/card#me"
        store.save_audit_log("key_pinned", webid=webid)
        logs = store.get_audit_logs()
        assert logs[0]["webid"] == webid

    def test_ip_stored(self, store):
        store.save_audit_log("rate_limited", ip="192.0.2.1")
        logs = store.get_audit_logs()
        assert logs[0]["ip"] == "192.0.2.1"

    def test_metadata_roundtrips_as_dict(self, store):
        meta = {"reason": "private_ip", "url": "http://10.0.0.1/relay"}
        store.save_audit_log("ssrf_blocked", metadata=meta)
        logs = store.get_audit_logs()
        assert isinstance(logs[0]["metadata"], dict)
        assert logs[0]["metadata"]["reason"] == "private_ip"

    def test_none_metadata_stored_as_none(self, store):
        store.save_audit_log("event", metadata=None)
        logs = store.get_audit_logs()
        assert logs[0]["metadata"] is None

    def test_timestamp_is_positive_float(self, store):
        store.save_audit_log("event")
        logs = store.get_audit_logs()
        assert isinstance(logs[0]["timestamp"], float)
        assert logs[0]["timestamp"] > 0

    def test_each_log_has_unique_id(self, store):
        for _ in range(5):
            store.save_audit_log("event")
        logs = store.get_audit_logs()
        ids = [l["id"] for l in logs]
        assert len(ids) == len(set(ids))


class TestGetAuditLogs:
    def test_newest_first_ordering(self, store):
        import time
        store.save_audit_log("first")
        time.sleep(0.01)
        store.save_audit_log("second")
        logs = store.get_audit_logs()
        assert logs[0]["event_type"] == "second"
        assert logs[1]["event_type"] == "first"

    def test_filter_by_event_type(self, store):
        store.save_audit_log("relay_rejected")
        store.save_audit_log("quota_exceeded")
        store.save_audit_log("relay_rejected")
        logs = store.get_audit_logs(event_type="relay_rejected")
        assert len(logs) == 2
        assert all(l["event_type"] == "relay_rejected" for l in logs)

    def test_limit_respected(self, store):
        for _ in range(20):
            store.save_audit_log("event")
        logs = store.get_audit_logs(limit=5)
        assert len(logs) == 5

    def test_empty_table_returns_empty_list(self, store):
        assert store.get_audit_logs() == []

    def test_filter_returns_empty_when_no_match(self, store):
        store.save_audit_log("other_event")
        logs = store.get_audit_logs(event_type="nonexistent")
        assert logs == []
