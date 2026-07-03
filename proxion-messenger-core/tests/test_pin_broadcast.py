"""Pinning a message must notify every room member, not just the pinner.

Before: _handle_pin_message sent message_pinned only back to the pinner, so a
pin didn't appear live for anyone else (unpin already broadcast to members).
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
    return any(json.loads(c[0][0]).get("type") == type_ for c in ws.send.call_args_list)


@pytest.fixture
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "pin.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


@pytest.mark.asyncio
async def test_pin_notifies_all_members(gateway, noauth_env):
    owner = Ed25519PrivateKey.generate()
    member = Ed25519PrivateKey.generate()
    owner_did = _did(owner)
    ws_owner = _mock_ws()
    ws_member = _mock_ws()
    await _register(gateway, ws_owner, owner_did)
    await _register(gateway, ws_member, _did(member))
    room_id = "room-pin"
    gateway._local_rooms[room_id] = {
        "creator_webid": owner_did, "members": {ws_owner, ws_member},
        "messages": [], "history_mode": "none", "name": "R", "code": "c",
    }

    ws_owner.send.reset_mock()
    ws_member.send.reset_mock()
    await gateway.process_command(ws_owner, {
        "cmd": "pin_message", "thread_id": room_id, "message_id": "m1", "content": "hi",
    })

    assert _got(ws_owner, "message_pinned"), "pinner should be notified"
    assert _got(ws_member, "message_pinned"), "other members must also be notified"


@pytest.mark.asyncio
async def test_non_owner_cannot_pin(gateway, noauth_env):
    owner = Ed25519PrivateKey.generate()
    member = Ed25519PrivateKey.generate()
    ws_owner = _mock_ws()
    ws_member = _mock_ws()
    await _register(gateway, ws_owner, _did(owner))
    await _register(gateway, ws_member, _did(member))
    room_id = "room-pin2"
    gateway._local_rooms[room_id] = {
        "creator_webid": _did(owner), "members": {ws_owner, ws_member},
        "messages": [], "history_mode": "none", "name": "R", "code": "c",
    }
    ws_member.send.reset_mock()
    await gateway.process_command(ws_member, {
        "cmd": "pin_message", "thread_id": room_id, "message_id": "m1",
    })
    assert _got(ws_member, "error")
    assert not _got(ws_member, "message_pinned")
