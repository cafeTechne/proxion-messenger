"""Tests: ban/mute federation — enforcement + propagation (Phase C1)."""
from __future__ import annotations
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    agent.identity_key.private_bytes = MagicMock(return_value=b"\x42" * 32)
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "t.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "t.db"))
    return gw


def _ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    return ws


# ── Enforcement: the security fix ──

@pytest.mark.asyncio
async def test_room_relay_drops_message_from_banned_sender(gateway):
    ws = _ws()
    room_id = "room-1"
    gateway._local_rooms[room_id] = {"name": "T", "members": {ws}}
    gateway._store.ban_room_member(room_id, "did:key:zBanned", "did:key:zOwner")

    status, body = await gateway._handle_room_relay({
        "room_id": room_id, "from_webid": "did:key:zBanned",
        "message_id": "m1", "content": "hi", "timestamp": "2026-06-12T00:00:00Z",
    })
    assert status.startswith("403")
    assert "banned" in body
    ws.send.assert_not_called()  # message never reached members


@pytest.mark.asyncio
async def test_room_relay_drops_message_from_muted_sender(gateway):
    ws = _ws()
    room_id = "room-2"
    gateway._local_rooms[room_id] = {"name": "T", "members": {ws}}
    gateway._store.mute_room_member(room_id, "did:key:zMuted", "did:key:zOwner")

    status, body = await gateway._handle_room_relay({
        "room_id": room_id, "from_webid": "did:key:zMuted",
        "message_id": "m2", "content": "hi", "timestamp": "2026-06-12T00:00:00Z",
    })
    assert status.startswith("403")
    assert "muted" in body


@pytest.mark.asyncio
async def test_room_relay_allows_unbanned_sender(gateway):
    ws = _ws()
    room_id = "room-3"
    gateway._local_rooms[room_id] = {"name": "T", "members": {ws}}
    # Not banned
    status, _ = await gateway._handle_room_relay({
        "room_id": room_id, "from_webid": "did:key:zOk",
        "message_id": "m3", "content": "hello", "timestamp": "2026-06-12T00:00:00Z",
    })
    assert status.startswith("200")
    ws.send.assert_called_once()


@pytest.mark.asyncio
async def test_reaction_relay_blocked_for_banned(gateway):
    ws = _ws()
    room_id = "room-4"
    gateway._local_rooms[room_id] = {"name": "T", "members": {ws}}
    gateway._store.ban_room_member(room_id, "did:key:zBanned", "did:key:zOwner")
    status, _ = await gateway._handle_room_reaction_relay({
        "room_id": room_id, "message_id": "m4", "emoji": "👍",
        "from_webid": "did:key:zBanned", "action": "add",
    })
    assert status.startswith("403")


# ── Propagation: federated gateways receive the action ──

@pytest.mark.asyncio
async def test_ban_relays_to_federated_gateways(gateway):
    owner_ws = _ws()
    room_id = "room-5"
    owner = "did:key:zOwner"
    gateway._client_webids[owner_ws] = owner
    gateway._local_rooms[room_id] = {"name": "T", "code": "x", "members": {owner_ws},
                                     "creator_webid": owner}
    gateway.clients.add(owner_ws)
    gateway._store.add_room_member(room_id, owner)
    gateway._store.add_federated_room_member(room_id, "did:key:zBob", "https://bob.example.com")

    tasks = []
    with patch("asyncio.create_task", side_effect=lambda c: tasks.append(c) or MagicMock()):
        await gateway._handle_ban_member(owner_ws, {
            "room_id": room_id, "webid": "did:key:zTarget", "reason": "spam",
        })
    assert gateway._store.is_room_banned(room_id, "did:key:zTarget")
    assert len(tasks) >= 1  # relayed to the federated gateway


# ── Inbound moderation relay applies + notifies ──

@pytest.mark.asyncio
async def test_moderation_relay_applies_ban_and_notifies(gateway):
    ws = _ws()
    room_id = "room-6"
    gateway._local_rooms[room_id] = {"name": "T", "members": {ws}}
    status, _ = await gateway._handle_room_moderation_relay({
        "room_id": room_id, "action": "ban", "webid": "did:key:zTarget",
        "from_webid": "did:key:zOwner", "reason": "abuse",
    })
    assert status.startswith("200")
    assert gateway._store.is_room_banned(room_id, "did:key:zTarget")
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "member_banned"
    assert sent["webid"] == "did:key:zTarget"


@pytest.mark.asyncio
async def test_moderation_relay_unmute(gateway):
    room_id = "room-7"
    gateway._local_rooms[room_id] = {"name": "T", "members": set()}
    gateway._store.mute_room_member(room_id, "did:key:zT", "did:key:zOwner")
    status, _ = await gateway._handle_room_moderation_relay({
        "room_id": room_id, "action": "unmute", "webid": "did:key:zT",
        "from_webid": "did:key:zOwner",
    })
    assert status.startswith("200")
    assert gateway._store.is_room_muted(room_id, "did:key:zT") is False
