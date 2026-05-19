"""Tests for schema version 36 migration."""
import sqlite3
import tempfile
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    return LocalStore(path)


class TestSchemaVersion36:
    def test_schema_version_bumped_to_36(self):
        assert LocalStore._SCHEMA_VERSION == 36

    def test_pod_capability_profiles_table_exists(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pod_capability_profiles'"
        ).fetchone()
        conn.close()
        assert row is not None, "pod_capability_profiles table must exist"

    def test_notification_fallback_events_table_exists(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='notification_fallback_events'"
        ).fetchone()
        conn.close()
        assert row is not None, "notification_fallback_events table must exist"

    def test_pod_capability_profile_crud(self, store):
        store.save_pod_capability_profile(
            pod_origin="https://alice.pod.example",
            notifications_supported=True,
            channel_types=["WebSocketChannel2023"],
            auth_requirements=["DPoP"],
            verification_source="runtime_probe",
        )
        profile = store.get_pod_capability_profile("https://alice.pod.example")
        assert profile is not None
        assert profile["notifications_supported"] == 1
        assert "WebSocketChannel2023" in profile["channel_types"]

    def test_notification_fallback_event_crud(self, store):
        eid = store.record_notification_fallback(
            pod_origin="https://alice.pod.example",
            reason_code="notifs_capability_absent",
            detail="HEAD probe returned no Link header",
        )
        assert eid
        events = store.get_notification_fallback_events(pod_origin="https://alice.pod.example")
        assert len(events) == 1
        assert events[0]["reason_code"] == "notifs_capability_absent"

    def test_notification_fallback_index_exists(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_notification_fallback_events_origin'"
        ).fetchone()
        conn.close()
        assert row is not None, "index on notification_fallback_events must exist"
