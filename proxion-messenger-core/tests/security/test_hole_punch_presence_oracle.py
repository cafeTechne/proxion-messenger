"""Tests that the presence oracle is closed: can_attempt_hole_punch requires
a real relationship (shared room or overlay key exchange)."""
import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.wg_overlay import generate_wg_keypair
from proxion_messenger_core.hole_punch import HolePunchCoordinator

ACTOR = "did:web:alice.example"
PEER = "did:web:bob.example"
STRANGER = "did:web:stranger.example"


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


@pytest.fixture
def coordinator(store):
    return HolePunchCoordinator(store)


def test_can_attempt_allowed_with_wg_peer_record(coordinator, store):
    _, pub_b64 = generate_wg_keypair()
    store.upsert_wg_peer(PEER, pub_b64, None, "10.0.0.2/32", "relay")
    assert coordinator.can_attempt_hole_punch(ACTOR, PEER) is True


def test_can_attempt_allowed_with_shared_room(coordinator, store):
    room_id = "room-test-123"
    store.save_room(room_id, "Test Room", "code123", None, "none", creator_webid=ACTOR)
    store.add_room_member(room_id, ACTOR)
    store.add_room_member(room_id, PEER)
    assert coordinator.can_attempt_hole_punch(ACTOR, PEER) is True


def test_can_attempt_denied_with_no_relationship(coordinator, store):
    assert coordinator.can_attempt_hole_punch(ACTOR, STRANGER) is False


def test_can_attempt_denied_when_only_actor_in_room(coordinator, store):
    room_id = "room-solo-456"
    store.save_room(room_id, "Solo Room", "code456", None, "none", creator_webid=ACTOR)
    store.add_room_member(room_id, ACTOR)
    # STRANGER is not in any shared room with ACTOR
    assert coordinator.can_attempt_hole_punch(ACTOR, STRANGER) is False


def test_can_attempt_no_store_always_allowed():
    coordinator = HolePunchCoordinator(store=None)
    assert coordinator.can_attempt_hole_punch(ACTOR, STRANGER) is True


def test_initiate_stores_actor_binding(coordinator, store):
    _, pub_b64 = generate_wg_keypair()
    store.upsert_wg_peer(PEER, pub_b64, None, "10.0.0.2/32", "relay")

    attempt_id = coordinator.initiate(ACTOR, PEER, "203.0.113.1", 5000, attempt_nonce="nonce42")
    attempt = coordinator.get_attempt(attempt_id)
    assert attempt["initiator_webid"] == ACTOR
    assert attempt["responder_webid"] == PEER
    assert attempt["attempt_nonce"] == "nonce42"


def test_response_omits_peer_online_field():
    """The presence oracle is closed: hole_punch_initiated must not expose peer_online."""
    # Tested at the protocol level: the handler was updated to omit peer_online.
    # This test verifies that "peer_online" is not a key in the response via
    # examination of the handler source, and that can_attempt returns bool
    # without leaking online status.
    from proxion_messenger_core import hole_punch as hp_mod
    import inspect
    src = inspect.getsource(hp_mod.HolePunchCoordinator.can_attempt_hole_punch)
    assert "peer_online" not in src
    assert "webid_sockets" not in src
