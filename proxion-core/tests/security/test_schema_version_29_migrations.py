"""Tests for schema version 29 migrations (Round 6)."""
import sqlite3
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "v29.db"))


class TestSchemaVersion29Migrations:
    def test_schema_version_bumped_to_29(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        conn.close()
        assert row[0] >= 35

    def test_messages_binding_index_exists(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_messages_id_from_thread'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_webhook_delivery_logs_table_exists(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='webhook_delivery_logs'"
        ).fetchone()
        conn.close()
        assert row is not None
