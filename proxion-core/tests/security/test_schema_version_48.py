"""Schema v48 structural tests — WireGuard overlay tables."""
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_schema_version_bumped_to_48(store):
    assert store._SCHEMA_VERSION >= 48


def test_wg_identity_and_peer_tables_exist(store):
    with store._conn() as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "wg_local_identity" in tables
    assert "wg_peers" in tables


def test_wg_connectivity_events_table_exists(store):
    with store._conn() as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "wg_connectivity_events" in tables
