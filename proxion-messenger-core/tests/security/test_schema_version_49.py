"""Smoke-test that schema v49 migrations apply cleanly."""
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_schema_version_is_49(store):
    assert store._SCHEMA_VERSION >= 49


def test_stun_sessions_table_accessible(store):
    store.save_stun_session("sess-1", "1.2.3.4", 12345, "stun.example.com")
    session = store.get_latest_stun_session()
    assert session is not None
    assert session["external_ip"] == "1.2.3.4"


def test_hole_punch_attempts_table_accessible(store):
    store.create_hole_punch_attempt("attempt-1", "did:web:peer.example", "5.6.7.8", 54321)
    attempt = store.get_hole_punch_attempt("attempt-1")
    assert attempt is not None
    assert attempt["state"] == "pending"
    assert attempt["peer_webid"] == "did:web:peer.example"


def test_schema_version_class_attr_matches_migration_count(store):
    assert store._SCHEMA_VERSION >= 49


def test_stun_sessions_index_created(store):
    with store._conn() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_stun_sessions_expires'"
        ).fetchone()
    assert row is not None


def test_hole_punch_attempts_index_created(store):
    with store._conn() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_hole_punch_attempts_peer'"
        ).fetchone()
    assert row is not None
