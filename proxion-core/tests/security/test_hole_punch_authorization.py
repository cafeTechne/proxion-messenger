"""Tests for actor-binding authorization on HolePunchCoordinator."""
import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.wg_overlay import generate_wg_keypair
from proxion_messenger_core.hole_punch import (
    HolePunchCoordinator,
    HolePunchForbidden,
    InvalidPunchTransition,
)

INITIATOR = "did:web:alice.example"
RESPONDER = "did:web:bob.example"
STRANGER = "did:web:stranger.example"


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


@pytest.fixture
def coordinator(store):
    return HolePunchCoordinator(store)


def _setup_attempt(coordinator, store):
    _, pub_b64 = generate_wg_keypair()
    store.upsert_wg_peer(RESPONDER, pub_b64, None, "10.0.0.2/32", "relay")
    attempt_id = coordinator.initiate(INITIATOR, RESPONDER, "1.2.3.4", 5000)
    coordinator.record_offer(attempt_id)
    return attempt_id


def test_initiator_can_record_peer_endpoint(coordinator, store):
    attempt_id = _setup_attempt(coordinator, store)
    coordinator.record_peer_endpoint(attempt_id, INITIATOR, "5.6.7.8", 9876)
    attempt = coordinator.get_attempt(attempt_id)
    assert attempt["peer_ip"] == "5.6.7.8"


def test_responder_can_record_peer_endpoint(coordinator, store):
    attempt_id = _setup_attempt(coordinator, store)
    coordinator.record_peer_endpoint(attempt_id, RESPONDER, "5.6.7.8", 9876)
    attempt = coordinator.get_attempt(attempt_id)
    assert attempt["peer_ip"] == "5.6.7.8"


def test_stranger_cannot_record_peer_endpoint(coordinator, store):
    attempt_id = _setup_attempt(coordinator, store)
    with pytest.raises(HolePunchForbidden):
        coordinator.record_peer_endpoint(attempt_id, STRANGER, "5.6.7.8", 9876)


def test_initiator_can_mark_succeeded(coordinator, store):
    attempt_id = _setup_attempt(coordinator, store)
    coordinator.record_peer_endpoint(attempt_id, INITIATOR, "5.6.7.8", 9876)
    coordinator.mark_succeeded(attempt_id, INITIATOR)
    assert coordinator.get_attempt(attempt_id)["state"] == "succeeded"


def test_responder_can_mark_succeeded(coordinator, store):
    attempt_id = _setup_attempt(coordinator, store)
    coordinator.record_peer_endpoint(attempt_id, RESPONDER, "5.6.7.8", 9876)
    coordinator.mark_succeeded(attempt_id, RESPONDER)
    assert coordinator.get_attempt(attempt_id)["state"] == "succeeded"


def test_stranger_cannot_mark_succeeded(coordinator, store):
    attempt_id = _setup_attempt(coordinator, store)
    coordinator.record_peer_endpoint(attempt_id, INITIATOR, "5.6.7.8", 9876)
    with pytest.raises(HolePunchForbidden):
        coordinator.mark_succeeded(attempt_id, STRANGER)


def test_stranger_cannot_mark_failed(coordinator, store):
    attempt_id = _setup_attempt(coordinator, store)
    with pytest.raises(HolePunchForbidden):
        coordinator.mark_failed(attempt_id, STRANGER)


def test_initiator_can_mark_failed(coordinator, store):
    attempt_id = _setup_attempt(coordinator, store)
    coordinator.mark_failed(attempt_id, INITIATOR)
    assert coordinator.get_attempt(attempt_id)["state"] == "failed"


def test_responder_can_mark_failed(coordinator, store):
    attempt_id = _setup_attempt(coordinator, store)
    coordinator.mark_failed(attempt_id, RESPONDER)
    assert coordinator.get_attempt(attempt_id)["state"] == "failed"
