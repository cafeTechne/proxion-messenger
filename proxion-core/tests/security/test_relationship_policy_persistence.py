"""Tests for cert policy version columns in relationships table (Round 5)."""
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


class TestRelationshipPolicyPersistence:
    def test_schema_version_bumped_to_29(self, store):
        import sqlite3
        conn = sqlite3.connect(store.db_path)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        conn.close()
        assert row[0] == 35

    def test_policy_index_exists_after_migration(self, store):
        import sqlite3
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_relationships_policy'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_dpop_seen_jti_table_exists(self, store):
        import sqlite3
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dpop_seen_jti'"
        ).fetchone()
        conn.close()
        assert row is not None
