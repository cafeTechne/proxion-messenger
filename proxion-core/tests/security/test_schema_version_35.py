"""R12: Schema version 35 migration tests."""
import sqlite3
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_schema_version_bumped_to_35(store):
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row[0] == 35


def test_compromise_recovery_tables_exist(store):
    with sqlite3.connect(store.db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "compromise_recovery_sessions" in tables
    assert "compromise_recovery_steps" in tables


def test_thread_participant_bindings_table_exists(store):
    with sqlite3.connect(store.db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "thread_participant_bindings" in tables


def test_policy_change_log_table_exists(store):
    with sqlite3.connect(store.db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "policy_change_log" in tables


def test_federation_quarantine_has_forensic_columns(store):
    with sqlite3.connect(store.db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(federation_quarantine)").fetchall()}
    assert "payload_sha256" in cols
    assert "source_ip" in cols
    assert "released_at" in cols
    assert "dropped_at" in cols


def test_compromise_recovery_session_index_exists(store):
    with sqlite3.connect(store.db_path) as conn:
        indices = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_compromise_sessions_status" in indices
