"""Round 8: relay delivery chain tamper-evidence tests."""
import time
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_relay_delivery_chain_appends_hashed_entries(store):
    peer_did = "did:key:chain-peer"
    # Two separate relay messages — each gets one chain entry
    store.append_relay_delivery_event("relay-msg-1", peer_did, "accepted")
    store.append_relay_delivery_event("relay-msg-2", peer_did, "delivered")

    result = store.verify_relay_delivery_chain(peer_did)
    assert result["valid"] is True
    assert result["entries"] == 2
    assert result["broken_at"] is None


def test_relay_delivery_chain_empty(store):
    result = store.verify_relay_delivery_chain("did:key:no-events")
    assert result["valid"] is True
    assert result["entries"] == 0


def test_relay_delivery_chain_detects_tamper(store, tmp_path):
    import sqlite3
    peer_did = "did:key:tamper-peer"
    store.append_relay_delivery_event("relay-x", peer_did, "accepted")
    store.append_relay_delivery_event("relay-y", peer_did, "delivered")

    # Tamper with the entry_hash of the second entry
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE relay_delivery_chain SET entry_hash = 'tampered' WHERE relay_id = 'relay-y'"
        )

    result = store.verify_relay_delivery_chain(peer_did)
    assert result["valid"] is False
    assert result["broken_at"] is not None


def test_relay_status_transitions_recorded(store):
    peer_did = "did:key:status-peer"
    # Three separate relay messages, each with a distinct status
    store.append_relay_delivery_event("r-accepted", peer_did, "accepted")
    store.append_relay_delivery_event("r-failed", peer_did, "failed")
    store.append_relay_delivery_event("r-delivered", peer_did, "delivered")

    result = store.verify_relay_delivery_chain(peer_did)
    assert result["valid"] is True
    assert result["entries"] == 3


def test_relay_delivery_chain_multiple_peers_independent(store):
    store.append_relay_delivery_event("relay-a1", "did:key:peer-a", "accepted")
    store.append_relay_delivery_event("relay-b1", "did:key:peer-b", "accepted")
    store.append_relay_delivery_event("relay-a2", "did:key:peer-a", "delivered")

    result_a = store.verify_relay_delivery_chain("did:key:peer-a")
    result_b = store.verify_relay_delivery_chain("did:key:peer-b")
    assert result_a["valid"] is True
    assert result_a["entries"] == 2
    assert result_b["valid"] is True
    assert result_b["entries"] == 1
