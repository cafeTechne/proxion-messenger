"""Schema v47 structural tests — recovery attempts, recovery codes, delivery state."""
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_schema_version_bumped_to_47(store):
    assert store._SCHEMA_VERSION >= 47


def test_dm_session_recovery_attempts_table_exists(store):
    with store._conn() as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "dm_session_recovery_attempts" in tables


def test_device_recovery_codes_table_exists(store):
    with store._conn() as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "device_recovery_codes" in tables
