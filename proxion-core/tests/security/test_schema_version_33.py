"""R10: Schema version 33 migration tests."""
import sqlite3
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_schema_version_bumped_to_33(store):
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row[0] == 35


def test_credential_anomalies_table_exists(store):
    with sqlite3.connect(store.db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "credential_anomalies" in tables


def test_identity_rollover_tables_exist(store):
    with sqlite3.connect(store.db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "identity_key_history" in tables
    assert "identity_rollover_events" in tables


def test_retention_locks_table_exists(store):
    with sqlite3.connect(store.db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "retention_locks" in tables


def test_credential_anomalies_index_exists(store):
    with sqlite3.connect(store.db_path) as conn:
        indices = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_credential_anomalies_created" in indices


def test_identity_rollover_index_exists(store):
    with sqlite3.connect(store.db_path) as conn:
        indices = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_identity_rollover_events_identity" in indices
