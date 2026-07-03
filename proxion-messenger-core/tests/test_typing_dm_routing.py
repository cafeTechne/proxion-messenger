"""DM typing indicators must reach the peer.

Regression: _handle_typing called get_dm_threads() with no owner_webid, which
queries WHERE owner_webid='' → always empty, so the peer never got the event.
"""
from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did


def _did(priv):
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock(); ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _got(ws, type_):
    return [json.loads(c[0][0]) for c in ws.send.call_args_list
            if json.loads(c[0][0]).get("type") == type_]


@pytest.fixture
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "typing.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


@pytest.mark.asyncio
async def test_dm_typing_reaches_peer_via_thread(gateway, noauth_env):
    alice = _did(Ed25519PrivateKey.generate())
    bob = _did(Ed25519PrivateKey.generate())
    ws_alice, ws_bob = _mock_ws(), _mock_ws()
    await _register(gateway, ws_alice, alice)
    await _register(gateway, ws_bob, bob)
    cert_id = "dm-cert-abc"
    gateway._store.save_dm_thread(cert_id, bob, "Bob", owner_webid=alice)

    ws_bob.send.reset_mock()
    await gateway.process_command(ws_alice, {"cmd": "typing", "cert_id": cert_id})
    evts = _got(ws_bob, "typing")
    assert evts, "peer must receive the typing event"
    assert evts[-1]["cert_id"] == cert_id
    assert evts[-1]["from_webid"] == alice


@pytest.mark.asyncio
async def test_local_dm_typing_uses_peer_did_directly(gateway, noauth_env):
    # Local DMs use the peer's DID as the cert_id/thread id — no dm_thread row.
    alice = _did(Ed25519PrivateKey.generate())
    bob = _did(Ed25519PrivateKey.generate())
    ws_alice, ws_bob = _mock_ws(), _mock_ws()
    await _register(gateway, ws_alice, alice)
    await _register(gateway, ws_bob, bob)

    ws_bob.send.reset_mock()
    await gateway.process_command(ws_alice, {"cmd": "typing", "cert_id": bob})  # cert_id == peer DID
    assert _got(ws_bob, "typing"), "peer must receive typing when cert_id is their DID"
