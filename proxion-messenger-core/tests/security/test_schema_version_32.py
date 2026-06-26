"""R9: Schema version 32 migration tests."""
import sqlite3
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_schema_version_bumped_to_32(store):
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row[0] >= 35


def test_peer_trust_disputes_table_exists(store):
    with sqlite3.connect(store.db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "peer_trust_disputes" in tables


def test_table_checksums_table_exists(store):
    with sqlite3.connect(store.db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "table_checksums" in tables


def test_federation_quarantine_table_exists(store):
    with sqlite3.connect(store.db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "federation_quarantine" in tables


def test_recovery_operations_has_fingerprint_column(store):
    with sqlite3.connect(store.db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(recovery_operations)").fetchall()}
    assert "requester_fingerprint" in cols


def test_recovery_operations_has_consumed_at_column(store):
    with sqlite3.connect(store.db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(recovery_operations)").fetchall()}
    assert "consumed_at" in cols


def test_peer_trust_disputes_index_exists(store):
    with sqlite3.connect(store.db_path) as conn:
        indices = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_peer_trust_disputes_peer" in indices
