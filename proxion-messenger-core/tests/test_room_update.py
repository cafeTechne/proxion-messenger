"""Renaming a room and setting its topic (update_room).

Before there was no way to change a room's name or topic after creating it.
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


async def _room_with_member(gw):
    owner, member = _mock_ws(), _mock_ws()
    await _register(gw, owner, _did())
    await _register(gw, member, _did())
    await gw.process_command(owner, {"cmd": "chat_room_create", "name": "Book club"})
    created = _last(owner, "room_created")
    await gw.process_command(member, {"cmd": "join_room", "code": created["code"]})
    assert _last(member, "room_joined")
    return owner, member, created["room_id"]


@pytest.mark.asyncio
async def test_owner_renames_room_and_sets_topic(tmp_path):
    gw = _gateway(tmp_path)
    owner, member, rid = await _room_with_member(gw)
    await gw.process_command(owner, {"cmd": "update_room", "room_id": rid,
                                     "name": "Reading group", "topic": "One book a month"})
    for ws in (owner, member):
        ev = _last(ws, "room_updated")
        assert ev and ev["name"] == "Reading group" and ev["topic"] == "One book a month"
    row = [r for r in gw._store.get_all_rooms() if r["room_id"] == rid][0]
    assert (row["name"], row["topic"]) == ("Reading group", "One book a month")


@pytest.mark.asyncio
async def test_topic_survives_a_restart_and_reaches_the_room_list(tmp_path):
    gw = _gateway(tmp_path)
    owner, _member, rid = await _room_with_member(gw)
    await gw.process_command(owner, {"cmd": "update_room", "room_id": rid, "topic": "Quiet please"})
    gw2 = _gateway(tmp_path)
    assert gw2._local_rooms[rid]["topic"] == "Quiet please"
    assert gw2._local_rooms[rid]["name"] == "Book club"


@pytest.mark.asyncio
async def test_plain_member_cannot_change_the_room(tmp_path):
    gw = _gateway(tmp_path)
    owner, member, rid = await _room_with_member(gw)
    await gw.process_command(member, {"cmd": "update_room", "room_id": rid, "name": "Mine now"})
    assert _last(owner, "room_updated") is None
    assert gw._local_rooms[rid]["name"] == "Book club"


@pytest.mark.asyncio
async def test_bad_name_and_long_topic_are_refused(tmp_path):
    gw = _gateway(tmp_path)
    owner, _member, rid = await _room_with_member(gw)
    await gw.process_command(owner, {"cmd": "update_room", "room_id": rid, "name": "   "})
    await gw.process_command(owner, {"cmd": "update_room", "room_id": rid, "topic": "x" * 301})
    assert _last(owner, "room_updated") is None
    assert gw._local_rooms[rid]["name"] == "Book club"
