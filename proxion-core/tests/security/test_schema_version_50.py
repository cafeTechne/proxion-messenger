"""Schema v50 — actor binding columns for hole punch and STUN session ownership."""
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_schema_version_is_50(store):
    assert store._SCHEMA_VERSION >= 50


def test_hole_punch_attempt_actor_columns_exist(store):
    store.create_hole_punch_attempt(
        "a1", "did:web:peer.example", "1.2.3.4", 5000,
        initiator_webid="did:web:alice.example",
        responder_webid="did:web:peer.example",
        attempt_nonce="abc123",
    )
    attempt = store.get_hole_punch_attempt("a1")
    assert attempt["initiator_webid"] == "did:web:alice.example"
    assert attempt["responder_webid"] == "did:web:peer.example"
    assert attempt["attempt_nonce"] == "abc123"


def test_stun_session_owner_columns_exist(store):
    store.save_stun_session(
        "s1", "203.0.113.5", 4321, "stun.example.com",
        owner_webid="did:web:alice.example",
        owner_device_id="device-1",
    )
    session = store.get_latest_stun_session_for_owner("did:web:alice.example", "device-1")
    assert session is not None
    assert session["owner_webid"] == "did:web:alice.example"
    assert session["owner_device_id"] == "device-1"


def test_hole_punch_actor_index_created(store):
    with store._conn() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_hole_punch_attempts_actor'"
        ).fetchone()
    assert row is not None


def test_stun_session_owner_index_created(store):
    with store._conn() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_stun_sessions_owner_expires'"
        ).fetchone()
    assert row is not None


def test_get_hole_punch_attempt_for_actor_returns_for_initiator(store):
    store.create_hole_punch_attempt(
        "a2", "did:web:peer.example", "1.2.3.4", 5000,
        initiator_webid="did:web:alice.example",
        responder_webid="did:web:peer.example",
    )
    result = store.get_hole_punch_attempt_for_actor("a2", "did:web:alice.example")
    assert result is not None


def test_get_hole_punch_attempt_for_actor_returns_for_responder(store):
    store.create_hole_punch_attempt(
        "a3", "did:web:peer.example", "1.2.3.4", 5000,
        initiator_webid="did:web:alice.example",
        responder_webid="did:web:peer.example",
    )
    result = store.get_hole_punch_attempt_for_actor("a3", "did:web:peer.example")
    assert result is not None


def test_get_hole_punch_attempt_for_actor_rejects_stranger(store):
    store.create_hole_punch_attempt(
        "a4", "did:web:peer.example", "1.2.3.4", 5000,
        initiator_webid="did:web:alice.example",
        responder_webid="did:web:peer.example",
    )
    result = store.get_hole_punch_attempt_for_actor("a4", "did:web:stranger.example")
    assert result is None
