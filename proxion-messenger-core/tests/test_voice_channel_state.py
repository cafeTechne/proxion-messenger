"""Room members outside a voice channel learn who is in it (voice_channel_state).

Before, only people already in the channel heard about joins and leaves, so the
rest of the room had no way to know a call was going on.
"""
from __future__ import annotations

import json

import pytest

from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did


def _did():
    return pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
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



@pytest.fixture(autouse=True)
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


def _gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "rooms.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


async def _room(gw):
    a, b = _mock_ws(), _mock_ws()
    a_did, b_did = _did(), _did()
    await _register(gw, a, a_did)
    await _register(gw, b, b_did)
    await gw.process_command(a, {"cmd": "chat_room_create", "name": "Standup"})
    created = _last(a, "room_created")
    await gw.process_command(b, {"cmd": "join_room", "code": created["code"]})
    return a, a_did, b, b_did, created["room_id"]


@pytest.mark.asyncio
async def test_members_outside_the_call_see_who_is_in_it(tmp_path):
    gw = _gateway(tmp_path)
    a, a_did, b, _b_did, rid = await _room(gw)
    await gw.process_command(a, {"cmd": "join_voice_channel", "room_id": rid})
    ev = _last(b, "voice_channel_state")
    assert ev and ev["room_id"] == rid and ev["participants"] == [a_did]

    await gw.process_command(a, {"cmd": "leave_voice_channel", "room_id": rid})
    assert _last(b, "voice_channel_state")["participants"] == []


@pytest.mark.asyncio
async def test_disconnecting_drops_you_from_the_count(tmp_path):
    gw = _gateway(tmp_path)
    a, _a_did, b, _b_did, rid = await _room(gw)
    await gw.process_command(a, {"cmd": "join_voice_channel", "room_id": rid})
    await gw._cleanup_voice_sessions(a)
    import asyncio
    await asyncio.sleep(0)
    assert _last(b, "voice_channel_state")["participants"] == []


@pytest.mark.asyncio
async def test_state_is_for_room_members_only(tmp_path):
    gw = _gateway(tmp_path)
    a, a_did, b, _b_did, rid = await _room(gw)
    await gw.process_command(a, {"cmd": "join_voice_channel", "room_id": rid})
    b.send.reset_mock()
    await gw.process_command(b, {"cmd": "get_voice_channel_state", "room_id": rid})
    assert _last(b, "voice_channel_state")["participants"] == [a_did]

    stranger = _mock_ws()
    stranger.remote_address = ("203.0.113.7", 5000)   # not loopback: no room adoption
    await _register(gw, stranger, _did())
    stranger.send.reset_mock()
    await gw.process_command(stranger, {"cmd": "get_voice_channel_state", "room_id": rid})
    assert _last(stranger, "voice_channel_state") is None
