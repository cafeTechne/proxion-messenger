"""Tests for the direct-mode promotion guard in HolePunchCoordinator."""
import time

import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.wg_overlay import generate_wg_keypair
from proxion_messenger_core.hole_punch import (
    HolePunchCoordinator,
    HolePunchForbidden,
    InvalidPunchTransition,
)
from proxion_messenger_core.transport_policy import select_transport, HANDSHAKE_STALE_SECONDS

INITIATOR = "did:web:alice.example"
RESPONDER = "did:web:bob.example"


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


@pytest.fixture
def coordinator(store):
    return HolePunchCoordinator(store)


def _add_relay_peer(store, webid="did:web:bob.example"):
    _, pub_b64 = generate_wg_keypair()
    store.upsert_wg_peer(webid, pub_b64, None, "10.0.0.2/32", "relay")


def test_direct_promotion_requires_accepted_state(coordinator, store):
    _add_relay_peer(store, RESPONDER)
    attempt_id = coordinator.initiate(INITIATOR, RESPONDER, "1.2.3.4", 5000)
    coordinator.record_offer(attempt_id)
    # Still in "offered" — must not promote
    with pytest.raises(InvalidPunchTransition):
        coordinator.mark_succeeded(attempt_id, INITIATOR)
    peer = store.get_wg_peer(RESPONDER)
    assert peer["path_mode"] == "relay"


def test_direct_promotion_requires_peer_endpoint_proof(coordinator, store):
    _add_relay_peer(store, RESPONDER)
    attempt_id = coordinator.initiate(INITIATOR, RESPONDER, "1.2.3.4", 5000)
    coordinator.record_offer(attempt_id)
    store.update_hole_punch_attempt(attempt_id, state="accepted")
    # No peer_ip recorded
    with pytest.raises(InvalidPunchTransition):
        coordinator.mark_succeeded(attempt_id, INITIATOR)
    peer = store.get_wg_peer(RESPONDER)
    assert peer["path_mode"] == "relay"


def test_failed_punch_keeps_relay(coordinator, store):
    _add_relay_peer(store, RESPONDER)
    attempt_id = coordinator.initiate(INITIATOR, RESPONDER, "1.2.3.4", 5000)
    coordinator.record_offer(attempt_id)
    coordinator.mark_failed(attempt_id, INITIATOR)
    assert select_transport(store, RESPONDER) == "relay"


def test_stale_direct_mode_reverts_to_relay(coordinator, store):
    _add_relay_peer(store, RESPONDER)
    attempt_id = coordinator.initiate(INITIATOR, RESPONDER, "1.2.3.4", 5000)
    coordinator.record_offer(attempt_id)
    coordinator.record_peer_endpoint(attempt_id, INITIATOR, "9.9.9.9", 1234)
    coordinator.mark_succeeded(attempt_id, INITIATOR)
    assert select_transport(store, RESPONDER) == "direct"

    stale_handshake = time.time() - HANDSHAKE_STALE_SECONDS - 1
    store.update_wg_peer_path_mode(RESPONDER, "direct", last_handshake_at=stale_handshake)
    assert select_transport(store, RESPONDER) == "relay"


def test_actor_check_blocks_unauthorized_promotion(coordinator, store):
    _add_relay_peer(store, RESPONDER)
    attempt_id = coordinator.initiate(INITIATOR, RESPONDER, "1.2.3.4", 5000)
    coordinator.record_offer(attempt_id)
    coordinator.record_peer_endpoint(attempt_id, INITIATOR, "9.9.9.9", 1234)
    with pytest.raises(HolePunchForbidden):
        coordinator.mark_succeeded(attempt_id, "did:web:stranger.example")
    peer = store.get_wg_peer(RESPONDER)
    assert peer["path_mode"] == "relay"


def test_successful_promotion_updates_handshake_timestamp(coordinator, store):
    _add_relay_peer(store, RESPONDER)
    before = time.time()
    attempt_id = coordinator.initiate(INITIATOR, RESPONDER, "1.2.3.4", 5000)
    coordinator.record_offer(attempt_id)
    coordinator.record_peer_endpoint(attempt_id, INITIATOR, "9.9.9.9", 1234)
    coordinator.mark_succeeded(attempt_id, INITIATOR)
    peer = store.get_wg_peer(RESPONDER)
    assert peer["last_handshake_at"] >= before
