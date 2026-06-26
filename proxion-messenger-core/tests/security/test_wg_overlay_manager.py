"""Tests for WgOverlayManager and generate_wg_keypair."""
import base64

import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.wg_overlay import WgOverlayManager, generate_wg_keypair


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


@pytest.fixture
def manager(store):
    return WgOverlayManager(store)


def test_overlay_generates_and_persists_local_identity(manager, store):
    identity = manager.ensure_local_identity()

    assert identity is not None
    assert "pubkey_b64" in identity
    assert "priv_wrapped_b64" in identity
    assert len(base64.b64decode(identity["pubkey_b64"])) == 32
    assert len(base64.b64decode(identity["priv_wrapped_b64"])) == 32

    same = manager.ensure_local_identity()
    assert same["pubkey_b64"] == identity["pubkey_b64"]


def test_peer_upsert_and_path_mode_update(manager, store):
    priv_b64, pub_b64 = generate_wg_keypair()
    manager.upsert_peer("did:web:bob.example", pub_b64, "1.2.3.4:51820", "10.0.0.2/32")

    peer = manager.get_peer("did:web:bob.example")
    assert peer is not None
    assert peer["peer_pubkey_b64"] == pub_b64
    assert peer["path_mode"] == "unknown"

    manager.update_path_mode("did:web:bob.example", "direct", reason="handshake_ok")
    peer_updated = manager.get_peer("did:web:bob.example")
    assert peer_updated["path_mode"] == "direct"


def test_connectivity_event_logged_on_mode_change(manager, store):
    priv_b64, pub_b64 = generate_wg_keypair()
    manager.upsert_peer("did:web:charlie.example", pub_b64, None, "10.0.0.3/32")
    manager.update_path_mode("did:web:charlie.example", "direct")
    manager.update_path_mode("did:web:charlie.example", "relay", reason="timeout")

    events = store.get_wg_connectivity_events("did:web:charlie.example")
    assert len(events) >= 2
    latest = events[0]
    assert latest["new_mode"] == "relay"
    assert latest["reason"] == "timeout"
