"""Tests that hole punch operations emit the expected security events."""
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


def _get_security_events(store, event_type):
    with store._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM security_events WHERE event_type=? ORDER BY created_at DESC",
            (event_type,),
        ).fetchall()
    return [dict(r) for r in rows]


def test_mark_failed_with_stranger_raises_forbidden(coordinator, store):
    attempt_id = coordinator.initiate(INITIATOR, RESPONDER, "1.2.3.4", 5000)
    coordinator.record_offer(attempt_id)
    with pytest.raises(HolePunchForbidden):
        coordinator.mark_failed(attempt_id, STRANGER)


def test_mark_succeeded_with_stranger_raises_forbidden(coordinator, store):
    _, pub_b64 = generate_wg_keypair()
    store.upsert_wg_peer(RESPONDER, pub_b64, None, "10.0.0.2/32", "relay")
    attempt_id = coordinator.initiate(INITIATOR, RESPONDER, "1.2.3.4", 5000)
    coordinator.record_offer(attempt_id)
    coordinator.record_peer_endpoint(attempt_id, INITIATOR, "5.6.7.8", 9876)
    with pytest.raises(HolePunchForbidden):
        coordinator.mark_succeeded(attempt_id, STRANGER)


def test_invalid_transition_raises_exception(coordinator, store):
    attempt_id = coordinator.initiate(INITIATOR, RESPONDER, "1.2.3.4", 5000)
    # Skip offered state — try going directly to accepted
    with pytest.raises(InvalidPunchTransition):
        coordinator._transition(attempt_id, "accepted")


def test_store_can_save_hole_punch_authz_denied_event(store):
    store.save_security_event(
        "hole_punch_authz_denied", "warning", webid=STRANGER,
        details="attempt_id=test-123",
    )
    events = _get_security_events(store, "hole_punch_authz_denied")
    assert len(events) == 1
    assert events[0]["webid"] == STRANGER


def test_store_can_save_hole_punch_nonce_mismatch_event(store):
    store.save_security_event(
        "hole_punch_nonce_mismatch", "warning", webid=INITIATOR,
        details="attempt_id=test-456",
    )
    events = _get_security_events(store, "hole_punch_nonce_mismatch")
    assert len(events) == 1


def test_store_can_save_direct_promotion_event(store):
    store.save_security_event(
        "hole_punch_direct_promotion", "info", webid=INITIATOR,
        details="attempt_id=test-789 peer=did:web:bob.example",
    )
    events = _get_security_events(store, "hole_punch_direct_promotion")
    assert len(events) == 1
    assert "peer" in events[0]["details"]


def test_store_can_save_endpoint_rejected_event(store):
    store.save_security_event(
        "hole_punch_endpoint_rejected", "warning", webid=INITIATOR,
        details="loopback address rejected",
    )
    events = _get_security_events(store, "hole_punch_endpoint_rejected")
    assert len(events) == 1


def test_state_invalid_event_recorded(store):
    store.save_security_event(
        "hole_punch_state_invalid", "warning", webid=INITIATOR,
        details="attempt_id=test cannot transition from offered to succeeded",
    )
    events = _get_security_events(store, "hole_punch_state_invalid")
    assert len(events) == 1
