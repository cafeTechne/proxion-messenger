"""Schema version 45 canonical test (Round 19)."""
import sqlite3
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_schema_version_bumped_to_45(store):
    """_SCHEMA_VERSION class attribute must equal 45."""
    assert LocalStore._SCHEMA_VERSION >= 45


def test_migrations_44_through_45_tables_exist(store):
    """All tables introduced in migrations 44–45 must exist."""
    conn = sqlite3.connect(store.db_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()

    expected = {
        "sender_keys",           # v44
        "push_subscriptions",    # v44
        "device_registrations",  # v45
        "room_seq_counters",     # v45
    }
    missing = expected - tables
    assert not missing, f"Missing tables: {missing}"


def test_schema_version_row_in_db(store):
    """schema_version table row must be >= 45."""
    conn = sqlite3.connect(store.db_path)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    conn.close()
    assert row is not None
    assert row[0] >= 45
