"""Tests: federated reaction relay."""
from __future__ import annotations
import json
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


@pytest.mark.asyncio
async def test_add_reaction_relays_to_federated_gateways(gateway):
    """_handle_add_reaction schedules relay to federated member gateways."""
    ws = _ws()
    room_id = "room-rx-1"
    sender_webid = "did:key:zAlice"
    gateway._client_webids[ws] = sender_webid
    gateway._local_rooms[room_id] = {"name": "T", "code": "x", "members": {ws}}
    gateway.clients.add(ws)
    gateway._store.add_room_member(room_id, sender_webid)
    gateway._store.add_federated_room_member(room_id, "did:key:zBob", "https://bob.example.com")

    tasks = []
    with patch("asyncio.create_task", side_effect=lambda c: tasks.append(c) or MagicMock()):
        await gateway._handle_add_reaction(ws, {
            "room_id": room_id, "message_id": "msg-1", "emoji": "👍",
        })

    # At least one create_task call for the federated relay
    assert len(tasks) > 0


@pytest.mark.asyncio
async def test_remove_reaction_relays_to_federated_gateways(gateway):
    """_handle_remove_reaction schedules relay to federated member gateways."""
    ws = _ws()
    room_id = "room-rx-2"
    sender_webid = "did:key:zAlice"
    gateway._client_webids[ws] = sender_webid
    gateway._local_rooms[room_id] = {"name": "T", "code": "x", "members": {ws}}
    gateway.clients.add(ws)
    gateway._store.add_room_member(room_id, sender_webid)
    gateway._store.add_federated_room_member(room_id, "did:key:zBob", "https://bob.example.com")
    gateway._store.save_reaction(room_id, "msg-1", "👍", sender_webid)

    tasks = []
    with patch("asyncio.create_task", side_effect=lambda c: tasks.append(c) or MagicMock()):
        await gateway._handle_remove_reaction(ws, {
            "room_id": room_id, "message_id": "msg-1", "emoji": "👍",
        })

    assert len(tasks) > 0


@pytest.mark.asyncio
async def test_room_reaction_relay_delivers_added(gateway):
    """_handle_room_reaction_relay delivers reaction_added to local members."""
    ws = _ws()
    room_id = "room-rx-3"
    gateway._local_rooms[room_id] = {"name": "T", "members": {ws}}
    gateway.clients.add(ws)

    status, _ = await gateway._handle_room_reaction_relay({
        "room_id": room_id, "message_id": "msg-2",
        "emoji": "❤️", "from_webid": "did:key:zBob", "action": "add",
    })

    assert status.startswith("200")
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "reaction_added"
    assert sent["emoji"] == "❤️"


@pytest.mark.asyncio
async def test_room_reaction_relay_delivers_removed(gateway):
    """_handle_room_reaction_relay delivers reaction_removed to local members."""
    ws = _ws()
    room_id = "room-rx-4"
    gateway._local_rooms[room_id] = {"name": "T", "members": {ws}}

    status, _ = await gateway._handle_room_reaction_relay({
        "room_id": room_id, "message_id": "msg-3",
        "emoji": "👎", "from_webid": "did:key:zBob", "action": "remove",
    })

    assert status.startswith("200")
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "reaction_removed"
