"""Tests that HolePunchCoordinator enforces state machine transitions."""
import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.wg_overlay import generate_wg_keypair
from proxion_messenger_core.hole_punch import (
    HolePunchCoordinator,
    InvalidPunchTransition,
    HolePunchForbidden,
    PUNCH_STATE_PENDING,
    PUNCH_STATE_OFFERED,
    PUNCH_STATE_SUCCEEDED,
    PUNCH_STATE_FAILED,
)

INITIATOR = "did:web:alice.example"
RESPONDER = "did:web:bob.example"


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


@pytest.fixture
def coordinator(store):
    return HolePunchCoordinator(store)


def _setup(coordinator, store):
    _, pub_b64 = generate_wg_keypair()
    store.upsert_wg_peer(RESPONDER, pub_b64, None, "10.0.0.2/32", "relay")
    attempt_id = coordinator.initiate(INITIATOR, RESPONDER, "1.2.3.4", 5000)
    return attempt_id


def test_transition_enforced_pending_to_offered(coordinator, store):
    attempt_id = _setup(coordinator, store)
    coordinator.record_offer(attempt_id)
    assert coordinator.get_attempt(attempt_id)["state"] == PUNCH_STATE_OFFERED


def test_invalid_transition_pending_to_succeeded_raises(coordinator, store):
    attempt_id = _setup(coordinator, store)
    # Manually try to jump from pending directly to succeeded via _transition
    with pytest.raises(InvalidPunchTransition):
        coordinator._transition(attempt_id, PUNCH_STATE_SUCCEEDED)


def test_invalid_transition_offered_to_succeeded_raises(coordinator, store):
    attempt_id = _setup(coordinator, store)
    coordinator.record_offer(attempt_id)
    with pytest.raises(InvalidPunchTransition):
        coordinator._transition(attempt_id, PUNCH_STATE_SUCCEEDED)


def test_no_transition_from_terminal_failed(coordinator, store):
    attempt_id = _setup(coordinator, store)
    coordinator.record_offer(attempt_id)
    coordinator.mark_failed(attempt_id, INITIATOR)
    with pytest.raises(InvalidPunchTransition):
        coordinator._transition(attempt_id, PUNCH_STATE_OFFERED)


def test_no_transition_from_terminal_succeeded(coordinator, store):
    attempt_id = _setup(coordinator, store)
    coordinator.record_offer(attempt_id)
    coordinator.record_peer_endpoint(attempt_id, INITIATOR, "5.6.7.8", 9876)
    coordinator.mark_succeeded(attempt_id, INITIATOR)
    with pytest.raises(InvalidPunchTransition):
        coordinator._transition(attempt_id, PUNCH_STATE_OFFERED)


def test_valid_full_chain_succeeds(coordinator, store):
    attempt_id = _setup(coordinator, store)
    coordinator.record_offer(attempt_id)
    coordinator.record_peer_endpoint(attempt_id, INITIATOR, "5.6.7.8", 9876)
    coordinator.mark_succeeded(attempt_id, INITIATOR)
    assert coordinator.get_attempt(attempt_id)["state"] == PUNCH_STATE_SUCCEEDED


def test_mark_succeeded_requires_accepted_state(coordinator, store):
    attempt_id = _setup(coordinator, store)
    coordinator.record_offer(attempt_id)
    # Still in "offered" state — cannot succeed yet
    with pytest.raises(InvalidPunchTransition):
        coordinator.mark_succeeded(attempt_id, INITIATOR)


def test_mark_succeeded_requires_peer_endpoint_proof(coordinator, store):
    attempt_id = _setup(coordinator, store)
    coordinator.record_offer(attempt_id)
    # Force to accepted without recording peer_ip
    store.update_hole_punch_attempt(attempt_id, state="accepted")
    with pytest.raises(InvalidPunchTransition):
        coordinator.mark_succeeded(attempt_id, INITIATOR)
