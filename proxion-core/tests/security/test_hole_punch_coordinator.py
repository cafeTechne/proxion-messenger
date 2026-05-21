"""Tests for HolePunchCoordinator state machine."""
import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.wg_overlay import WgOverlayManager, generate_wg_keypair
from proxion_messenger_core.hole_punch import (
    HolePunchCoordinator,
    PUNCH_STATE_PENDING,
    PUNCH_STATE_OFFERED,
    PUNCH_STATE_ACCEPTED,
    PUNCH_STATE_SUCCEEDED,
    PUNCH_STATE_FAILED,
)


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


@pytest.fixture
def coordinator(store):
    return HolePunchCoordinator(store)


def test_initiate_creates_pending_attempt(coordinator):
    attempt_id = coordinator.initiate("did:web:peer.example", "203.0.113.1", 12345)
    attempt = coordinator.get_attempt(attempt_id)
    assert attempt is not None
    assert attempt["state"] == PUNCH_STATE_PENDING
    assert attempt["peer_webid"] == "did:web:peer.example"
    assert attempt["local_ip"] == "203.0.113.1"
    assert attempt["local_port"] == 12345


def test_record_offer_advances_to_offered(coordinator):
    attempt_id = coordinator.initiate("did:web:peer.example", "1.2.3.4", 5000)
    coordinator.record_offer(attempt_id)
    attempt = coordinator.get_attempt(attempt_id)
    assert attempt["state"] == PUNCH_STATE_OFFERED


def test_record_peer_endpoint_advances_to_accepted(coordinator):
    attempt_id = coordinator.initiate("did:web:peer.example", "1.2.3.4", 5000)
    coordinator.record_offer(attempt_id)
    coordinator.record_peer_endpoint(attempt_id, "5.6.7.8", 9876)
    attempt = coordinator.get_attempt(attempt_id)
    assert attempt["state"] == PUNCH_STATE_ACCEPTED
    assert attempt["peer_ip"] == "5.6.7.8"
    assert attempt["peer_port"] == 9876


def test_mark_succeeded_sets_terminal_state(coordinator, store):
    _, pub_b64 = generate_wg_keypair()
    store.upsert_wg_peer("did:web:peer.example", pub_b64, None, "10.0.0.2/32", "relay")

    attempt_id = coordinator.initiate("did:web:peer.example", "1.2.3.4", 5000)
    coordinator.record_offer(attempt_id)
    coordinator.record_peer_endpoint(attempt_id, "5.6.7.8", 9876)
    coordinator.mark_succeeded(attempt_id)

    attempt = coordinator.get_attempt(attempt_id)
    assert attempt["state"] == PUNCH_STATE_SUCCEEDED
    assert attempt["completed_at"] is not None


def test_mark_succeeded_upgrades_path_to_direct(coordinator, store):
    _, pub_b64 = generate_wg_keypair()
    store.upsert_wg_peer("did:web:peer.example", pub_b64, None, "10.0.0.2/32", "relay")

    attempt_id = coordinator.initiate("did:web:peer.example", "1.2.3.4", 5000)
    coordinator.record_offer(attempt_id)
    coordinator.record_peer_endpoint(attempt_id, "5.6.7.8", 9876)
    coordinator.mark_succeeded(attempt_id)

    peer = store.get_wg_peer("did:web:peer.example")
    assert peer is not None
    assert peer["path_mode"] == "direct"
    assert peer["last_handshake_at"] is not None


def test_mark_failed_sets_terminal_state(coordinator):
    attempt_id = coordinator.initiate("did:web:peer.example", "1.2.3.4", 5000)
    coordinator.record_offer(attempt_id)
    coordinator.mark_failed(attempt_id)
    attempt = coordinator.get_attempt(attempt_id)
    assert attempt["state"] == PUNCH_STATE_FAILED
    assert attempt["completed_at"] is not None


def test_get_attempt_returns_none_for_unknown(coordinator):
    result = coordinator.get_attempt("nonexistent-id")
    assert result is None
