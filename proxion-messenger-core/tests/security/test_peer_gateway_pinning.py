"""Round 8: peer gateway pinning tests."""
import time
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_first_seen_gateway_is_pinned(store):
    peer_did = "did:key:first-peer"
    store.upsert_peer_gateway_pin(
        peer_did=peer_did,
        pinned_gateway_url="https://gw-a.example.com",
        pinned_at=time.time(),
        last_seen_gateway_url="https://gw-a.example.com",
        last_seen_at=time.time(),
        pending_change=False,
    )
    pin = store.get_peer_gateway_pin(peer_did)
    assert pin is not None
    assert pin["pinned_gateway_url"] == "https://gw-a.example.com"
    assert pin["pending_change"] == 0


def test_gateway_change_creates_pending_request(store):
    peer_did = "did:key:change-peer"
    now = time.time()
    store.upsert_peer_gateway_pin(
        peer_did=peer_did,
        pinned_gateway_url="https://gw-a.example.com",
        pinned_at=now,
        last_seen_gateway_url="https://gw-b.example.com",
        last_seen_at=now,
        pending_change=True,
    )
    import uuid
    store.record_peer_gateway_change_request(
        id=str(uuid.uuid4()),
        peer_did=peer_did,
        old_gateway_url="https://gw-a.example.com",
        new_gateway_url="https://gw-b.example.com",
        observed_at=now,
    )
    requests = store.list_peer_gateway_change_requests(peer_did=peer_did)
    assert len(requests) == 1
    assert requests[0]["old_gateway_url"] == "https://gw-a.example.com"
    assert requests[0]["new_gateway_url"] == "https://gw-b.example.com"
    assert requests[0]["approved"] == 0


def test_unapproved_gateway_change_blocks_outbound_relay(store):
    """Pin is set to pending — pinned URL stays the same (change not approved)."""
    peer_did = "did:key:block-peer"
    now = time.time()
    store.upsert_peer_gateway_pin(
        peer_did=peer_did,
        pinned_gateway_url="https://gw-orig.example.com",
        pinned_at=now,
        last_seen_gateway_url="https://gw-new.example.com",
        last_seen_at=now,
        pending_change=True,
    )
    pin = store.get_peer_gateway_pin(peer_did)
    # Pinned URL should still be original until approved
    assert pin["pinned_gateway_url"] == "https://gw-orig.example.com"
    assert pin["pending_change"] == 1


def test_approve_peer_gateway_change(store):
    peer_did = "did:key:approve-peer"
    now = time.time()
    import uuid
    store.upsert_peer_gateway_pin(
        peer_did=peer_did,
        pinned_gateway_url="https://gw-old.example.com",
        pinned_at=now,
        last_seen_gateway_url="https://gw-new.example.com",
        last_seen_at=now,
        pending_change=True,
    )
    store.record_peer_gateway_change_request(
        id=str(uuid.uuid4()),
        peer_did=peer_did,
        old_gateway_url="https://gw-old.example.com",
        new_gateway_url="https://gw-new.example.com",
        observed_at=now,
    )
    result = store.approve_peer_gateway_change(peer_did)
    assert result is True
    pin = store.get_peer_gateway_pin(peer_did)
    assert pin["pinned_gateway_url"] == "https://gw-new.example.com"
    assert pin["pending_change"] == 0


def test_list_peer_gateway_change_requests_empty(store):
    requests = store.list_peer_gateway_change_requests(peer_did="did:key:unknown")
    assert requests == []
