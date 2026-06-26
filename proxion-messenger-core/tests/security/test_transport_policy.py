"""Tests for transport selection policy (DB-state-based decisions)."""
import time

import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.transport_policy import (
    select_transport,
    requires_sealed_sender,
    HANDSHAKE_STALE_SECONDS,
)
from proxion_messenger_core.wg_overlay import generate_wg_keypair


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_direct_path_selected_when_peer_has_recent_handshake(store):
    _, pub_b64 = generate_wg_keypair()
    store.upsert_wg_peer("did:web:bob.example", pub_b64, None, "10.0.0.2/32", "direct")
    store.update_wg_peer_path_mode("did:web:bob.example", "direct", last_handshake_at=time.time())

    assert select_transport(store, "did:web:bob.example") == "direct"
    assert requires_sealed_sender(store, "did:web:bob.example") is False


def test_relay_selected_when_direct_handshake_stale(store):
    _, pub_b64 = generate_wg_keypair()
    store.upsert_wg_peer("did:web:carol.example", pub_b64, None, "10.0.0.3/32", "direct")
    stale_ts = time.time() - (HANDSHAKE_STALE_SECONDS + 60)
    store.update_wg_peer_path_mode("did:web:carol.example", "direct", last_handshake_at=stale_ts)

    assert select_transport(store, "did:web:carol.example") == "relay"
    assert requires_sealed_sender(store, "did:web:carol.example") is True


def test_none_returned_when_no_peer_record(store):
    assert select_transport(store, "did:web:unknown.example") == "none"
    assert requires_sealed_sender(store, "did:web:unknown.example") is False
