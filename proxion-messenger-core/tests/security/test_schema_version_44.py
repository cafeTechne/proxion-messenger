"""Schema version 44 canonical test (Round 18)."""
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_schema_version_bumped_to_44(store):
    """_SCHEMA_VERSION class attribute must equal 44."""
    assert LocalStore._SCHEMA_VERSION >= 44


def test_migrations_40_through_44_tables_exist(store):
    """All tables introduced in migrations 40–44 must exist."""
    import sqlite3
    conn = sqlite3.connect(store.db_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()

    expected = {
        "dm_sessions",           # v40
        "dm_prekeys",            # v40
        "rate_limit_buckets",    # v41
        "message_receipts",      # v42
        "contact_verifications", # v43
        "sender_keys",           # v44
        "push_subscriptions",    # v44
    }
    missing = expected - tables
    assert not missing, f"Missing tables: {missing}"


def test_schema_version_row_in_db(store):
    """schema_version table row must be >= 44."""
    import sqlite3
    conn = sqlite3.connect(store.db_path)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    conn.close()
    assert row is not None
    assert row[0] >= 44
