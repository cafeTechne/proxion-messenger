"""Tests that a successful hole punch promotes transport to direct."""
import time

import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.wg_overlay import WgOverlayManager, generate_wg_keypair
from proxion_messenger_core.hole_punch import HolePunchCoordinator
from proxion_messenger_core.transport_policy import select_transport, requires_sealed_sender


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _add_relay_peer(store, webid):
    _, pub_b64 = generate_wg_keypair()
    store.upsert_wg_peer(webid, pub_b64, None, "10.0.0.1/32", "relay")


def test_relay_transport_before_punch(store):
    webid = "did:web:peer.example"
    _add_relay_peer(store, webid)
    assert select_transport(store, webid) == "relay"
    assert requires_sealed_sender(store, webid) is True


def test_direct_transport_after_successful_punch(store):
    webid = "did:web:peer.example"
    _add_relay_peer(store, webid)

    coordinator = HolePunchCoordinator(store)
    attempt_id = coordinator.initiate(webid, "203.0.113.1", 54321)
    coordinator.record_offer(attempt_id)
    coordinator.record_peer_endpoint(attempt_id, "198.51.100.5", 12345)
    coordinator.mark_succeeded(attempt_id)

    assert select_transport(store, webid) == "direct"
    assert requires_sealed_sender(store, webid) is False


def test_relay_transport_after_failed_punch(store):
    webid = "did:web:peer.example"
    _add_relay_peer(store, webid)

    coordinator = HolePunchCoordinator(store)
    attempt_id = coordinator.initiate(webid, "203.0.113.1", 54321)
    coordinator.record_offer(attempt_id)
    coordinator.mark_failed(attempt_id)

    assert select_transport(store, webid) == "relay"
    assert requires_sealed_sender(store, webid) is True


def test_direct_transport_becomes_relay_when_handshake_stale(store):
    from proxion_messenger_core.transport_policy import HANDSHAKE_STALE_SECONDS
    webid = "did:web:peer.example"
    _add_relay_peer(store, webid)

    coordinator = HolePunchCoordinator(store)
    attempt_id = coordinator.initiate(webid, "203.0.113.1", 54321)
    coordinator.record_offer(attempt_id)
    coordinator.record_peer_endpoint(attempt_id, "198.51.100.5", 12345)
    coordinator.mark_succeeded(attempt_id)

    # Manually backdate the handshake to simulate stale state
    stale_time = time.time() - HANDSHAKE_STALE_SECONDS - 1
    store.update_wg_peer_path_mode(webid, "direct", last_handshake_at=stale_time)

    assert select_transport(store, webid) == "relay"


def test_none_transport_when_no_peer_record(store):
    assert select_transport(store, "did:web:unknown.example") == "none"
