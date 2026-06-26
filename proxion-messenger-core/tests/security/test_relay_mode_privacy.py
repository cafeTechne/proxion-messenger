"""Tests for relay-mode sealed-sender enforcement via transport_policy."""
import time

import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.transport_policy import requires_sealed_sender, HANDSHAKE_STALE_SECONDS
from proxion_messenger_core.wg_overlay import generate_wg_keypair


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_requires_sealed_sender_true_when_relay_mode(store):
    _, pub_b64 = generate_wg_keypair()
    store.upsert_wg_peer("did:web:peer.example", pub_b64, None, "10.0.0.2/32", "relay")

    assert requires_sealed_sender(store, "did:web:peer.example") is True


def test_requires_sealed_sender_false_when_direct_mode(store):
    _, pub_b64 = generate_wg_keypair()
    store.upsert_wg_peer("did:web:peer2.example", pub_b64, None, "10.0.0.3/32", "direct")
    store.update_wg_peer_path_mode("did:web:peer2.example", "direct", last_handshake_at=time.time())

    assert requires_sealed_sender(store, "did:web:peer2.example") is False


def test_requires_sealed_sender_false_when_no_peer_record(store):
    assert requires_sealed_sender(store, "did:web:ghost.example") is False
