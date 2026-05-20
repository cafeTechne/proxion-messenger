"""Schema version 46 canonical test (Round 20)."""
import sqlite3
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_schema_version_bumped_to_46(store):
    """_SCHEMA_VERSION class attribute must equal 46."""
    assert LocalStore._SCHEMA_VERSION == 46


def test_round20_tables_exist(store):
    """All tables introduced in Round 20 must exist."""
    conn = sqlite3.connect(store.db_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()

    expected = {
        "dm_device_deliveries",  # v46
        "idempotency_ops",       # v46
        "catchup_watermarks",    # v46
    }
    missing = expected - tables
    assert not missing, f"Missing tables: {missing}"


def test_schema_version_row_in_db(store):
    """schema_version table row must be >= 46."""
    conn = sqlite3.connect(store.db_path)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    conn.close()
    assert row is not None
    assert row[0] >= 46
