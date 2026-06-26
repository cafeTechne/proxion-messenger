"""Schema v39: evidence_verification_records table."""
import pytest
from proxion_messenger_core.local_store import LocalStore


def test_schema_version_is_39():
    assert LocalStore._SCHEMA_VERSION >= 39


def test_evidence_verification_table_exists(tmp_path):
    db = tmp_path / "test.db"
    store = LocalStore(str(db))
    with store._conn() as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='evidence_verification_records'"
        )
        assert cur.fetchone() is not None


def test_evidence_verification_index_exists(tmp_path):
    db = tmp_path / "test.db"
    store = LocalStore(str(db))
    with store._conn() as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_evidence_verification_records_time'"
        )
        assert cur.fetchone() is not None
