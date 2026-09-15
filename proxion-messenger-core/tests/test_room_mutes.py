"""Tests: room mute functionality."""
from __future__ import annotations
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock
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


def test_mute_persists_and_detected(store):
    store.mute_room_member("room-1", "did:key:zBob", "did:key:zAlice")
    assert store.is_room_muted("room-1", "did:key:zBob") is True


def test_expired_mute_not_active(store):
    past = time.time() - 10
    store.mute_room_member("room-2", "did:key:zBob", "did:key:zAlice", expires_at=past)
    assert store.is_room_muted("room-2", "did:key:zBob") is False


def test_unmute_lifts_mute(store):
    store.mute_room_member("room-3", "did:key:zBob", "did:key:zAlice")
    store.unmute_room_member("room-3", "did:key:zBob")
    assert store.is_room_muted("room-3", "did:key:zBob") is False


@pytest.mark.asyncio
async def test_send_room_rejects_muted_member(gateway):
    ws = _ws()
    room_id = "room-mute-1"
    muted_webid = "did:key:zMuted"
    gateway._client_webids[ws] = muted_webid
    gateway._local_rooms[room_id] = {"name": "T", "code": "x",
                                      "members": {ws}, "creator_webid": "did:key:zOwner"}
    gateway.clients.add(ws)
    gateway._store.add_room_member(room_id, muted_webid)
    gateway._store.mute_room_member(room_id, muted_webid, "did:key:zOwner")

    await gateway._handle_send_room(ws, {"room_id": room_id, "content": "hello"})

    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("message") == "you_are_muted"


def _muted_room(gateway, room_id, muted_webid):
    ws = _ws()
    gateway._client_webids[ws] = muted_webid
    gateway._local_rooms[room_id] = {"name": "T", "code": "x",
                                     "members": {ws}, "creator_webid": "did:key:zOwner"}
    gateway.clients.add(ws)
    gateway._store.add_room_member(room_id, muted_webid)
    gateway._store.mute_room_member(room_id, muted_webid, "did:key:zOwner")
    return ws


@pytest.mark.asyncio
async def test_add_reaction_rejects_muted_member(gateway):
    ws = _muted_room(gateway, "room-mute-react", "did:key:zMuted")
    await gateway._handle_add_reaction(
        ws, {"room_id": "room-mute-react", "message_id": "m1", "emoji": "\U0001F44D"})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("message") == "you_are_muted"


@pytest.mark.asyncio
async def test_remove_reaction_rejects_muted_member(gateway):
    ws = _muted_room(gateway, "room-mute-unreact", "did:key:zMuted")
    await gateway._handle_remove_reaction(
        ws, {"room_id": "room-mute-unreact", "message_id": "m1", "emoji": "\U0001F44D"})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("message") == "you_are_muted"


@pytest.mark.asyncio
async def test_edit_local_message_rejects_muted_member(gateway):
    ws = _muted_room(gateway, "room-mute-edit", "did:key:zMuted")
    await gateway._handle_edit_local_message(
        ws, {"thread_id": "room-mute-edit", "message_id": "m1", "content": "rewritten"})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("message") == "you_are_muted"


@pytest.mark.asyncio
async def test_upload_sender_key_requires_room_membership(gateway):
    # A caller who is not a member of the room cannot write sender-key rows for it.
    gateway._force_auth = True
    ws = _ws()
    gateway._client_webids[ws] = "did:key:zStranger"
    gateway._local_rooms["room-sk"] = {"name": "T", "code": "x",
                                       "members": set(), "creator_webid": "did:key:zOwner"}
    gateway.clients.add(ws)
    await gateway._handle_upload_sender_key(
        ws, {"room_id": "room-sk", "chain_key_b64": "AAAA", "iteration": 0})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("message") == "Not a member of this room"
