"""Tests: room ban functionality."""
from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    agent.identity_key.private_bytes = MagicMock(return_value=b"\x42" * 32)
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "test.db"))
    return gw


def _ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    return ws


def test_ban_persists_and_is_detected(store):
    store.ban_room_member("room-1", "did:key:zBob", "did:key:zAlice", "spamming")
    assert store.is_room_banned("room-1", "did:key:zBob") is True
    assert store.is_room_banned("room-1", "did:key:zCarol") is False


def test_unban_lifts_ban(store):
    store.ban_room_member("room-2", "did:key:zBob", "did:key:zAlice")
    store.unban_room_member("room-2", "did:key:zBob")
    assert store.is_room_banned("room-2", "did:key:zBob") is False


def test_get_room_bans_returns_list(store):
    store.ban_room_member("room-3", "did:key:zBob", "did:key:zAlice", "reason1")
    store.ban_room_member("room-3", "did:key:zCarol", "did:key:zAlice", "reason2")
    bans = store.get_room_bans("room-3")
    assert len(bans) == 2
    assert any(b["banned_did"] == "did:key:zBob" for b in bans)


@pytest.mark.asyncio
async def test_handle_ban_member_broadcasts_event(gateway):
    owner_ws = _ws()
    member_ws = _ws()
    room_id = "room-ban-1"
    owner_webid = "did:key:zOwner"
    target_webid = "did:key:zTarget"
    gateway._client_webids[owner_ws] = owner_webid
    gateway._local_rooms[room_id] = {"name": "T", "code": "x",
                                      "members": {owner_ws, member_ws},
                                      "creator_webid": owner_webid}
    gateway.clients.add(owner_ws)
    gateway.clients.add(member_ws)
    gateway._store.add_room_member(room_id, owner_webid)

    await gateway._handle_ban_member(owner_ws, {
        "room_id": room_id, "webid": target_webid, "reason": "test ban",
    })

    assert gateway._store.is_room_banned(room_id, target_webid)
    all_sends = []
    for ws in [owner_ws, member_ws]:
        for call in ws.send.call_args_list:
            all_sends.append(json.loads(call[0][0]))
    banned_events = [e for e in all_sends if e.get("type") == "member_banned"]
    assert len(banned_events) > 0


@pytest.mark.asyncio
async def test_join_room_rejects_banned_user(gateway):
    ws = _ws()
    room_id = "room-ban-2"
    banned_webid = "did:key:zBanned"
    code = "joincode"
    gateway._client_webids[ws] = banned_webid
    gateway._local_rooms[room_id] = {"name": "T", "code": code, "members": set()}
    gateway._room_codes[code] = room_id
    gateway._store.add_room_member(room_id, "did:key:zOwner")
    gateway._store.ban_room_member(room_id, banned_webid, "did:key:zOwner")

    await gateway._handle_join_room(ws, {"code": code})

    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("message") == "banned_from_room"
