"""Round 8: schema version 31 migration tests."""
import sqlite3
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_schema_version_bumped_to_31(store):
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row[0] == 35


def test_peer_gateway_pin_tables_exist(store):
    with sqlite3.connect(store.db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "peer_gateway_pins" in tables
    assert "peer_gateway_change_requests" in tables


def test_relay_delivery_chain_table_exists(store):
    with sqlite3.connect(store.db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "relay_delivery_chain" in tables


def test_invite_counter_tables_exist(store):
    with sqlite3.connect(store.db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "invite_pair_counters" in tables
    assert "invite_source_counters" in tables


def test_recovery_operations_table_exists(store):
    with sqlite3.connect(store.db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "recovery_operations" in tables


def test_integrity_ok_flag_set_on_healthy_db(store):
    assert store._integrity_ok is True
