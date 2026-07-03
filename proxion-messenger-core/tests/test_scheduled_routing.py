"""Scheduled messages must deliver to the right thread type.

Before: the scheduler always delivered via send_room, so a scheduled DM (the UI
offers scheduling in DMs too) routed to a nonexistent room and was silently lost.
"""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did


def _did(priv):
    return pub_key_to_did(priv.public_key().public_bytes_raw())


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "sched.db")),
    )


def test_local_room_routes_to_send_room(gateway):
    gateway._local_rooms["room-1"] = {"members": set(), "messages": [], "history_mode": "none"}
    cmd = gateway._scheduled_delivery_command("room-1", "did:key:zAlice", "hi")
    assert cmd == {"cmd": "send_room", "room_id": "room-1", "content": "hi"}


def test_local_dm_routes_to_local_dm_with_peer(gateway):
    alice, bob = _did(Ed25519PrivateKey.generate()), _did(Ed25519PrivateKey.generate())
    gateway._store.save_dm_thread("dm-thread-1", bob, "Bob", owner_webid=alice)
    cmd = gateway._scheduled_delivery_command("dm-thread-1", alice, "hey bob")
    assert cmd == {"cmd": "local_dm", "target_webid": bob, "content": "hey bob"}


def test_cert_dm_routes_to_send_dm(gateway):
    import types
    gateway.dm_clients["cert-9"] = (types.SimpleNamespace(subject="abc"), object())
    cmd = gateway._scheduled_delivery_command("cert-9", "did:key:zAlice", "yo")
    assert cmd == {"cmd": "send_dm", "cert_id": "cert-9", "content": "yo"}


def test_unknown_thread_returns_none_not_send_room(gateway):
    # The old bug: this would have been delivered as send_room and lost.
    assert gateway._scheduled_delivery_command("nope", "did:key:zAlice", "x") is None
