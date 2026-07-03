"""Joining a room's voice channel requires room membership.

Regression: _handle_join_voice_channel never checked membership, so anyone who
knew a room id could join its voice channel and exchange voice with its members.
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


def _last(ws, type_):
    for c in reversed(ws.send.call_args_list):
        m = json.loads(c[0][0])
        if m.get("type") == type_:
            return m
    return None


@pytest.fixture
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "vc.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


@pytest.mark.asyncio
async def test_non_member_cannot_join_room_voice_channel(gateway, noauth_env):
    owner = _did(Ed25519PrivateKey.generate())
    outsider = _did(Ed25519PrivateKey.generate())
    ws_owner, ws_out = _mock_ws(), _mock_ws()
    await _register(gateway, ws_owner, owner)
    await _register(gateway, ws_out, outsider)
    room_id = "room-vc"
    gateway._local_rooms[room_id] = {
        "creator_webid": owner, "members": {ws_owner},
        "messages": [], "history_mode": "none", "name": "R", "code": "c",
    }

    # Outsider (not a room member) tries to join the voice channel.
    await gateway.process_command(ws_out, {"cmd": "join_voice_channel", "channel_id": room_id})
    assert (_last(ws_out, "error") or {}).get("message") == "not_a_room_member"
    assert outsider not in gateway._voice_channels.get(room_id, {}).get("members", {})


@pytest.mark.asyncio
async def test_member_can_join_room_voice_channel(gateway, noauth_env):
    owner = _did(Ed25519PrivateKey.generate())
    ws_owner = _mock_ws()
    await _register(gateway, ws_owner, owner)
    room_id = "room-vc2"
    gateway._local_rooms[room_id] = {
        "creator_webid": owner, "members": {ws_owner},
        "messages": [], "history_mode": "none", "name": "R", "code": "c",
    }
    await gateway.process_command(ws_owner, {"cmd": "join_voice_channel", "channel_id": room_id})
    assert owner in gateway._voice_channels.get(room_id, {}).get("members", {})
