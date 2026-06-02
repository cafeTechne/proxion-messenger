"""Tests: federated message edit/delete relay."""
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
async def test_delete_relays_to_federated_gateways(gateway):
    """_handle_delete_local_message schedules relay to federated gateways."""
    ws = _ws()
    room_id = "room-del-1"
    caller_webid = "did:key:zAlice"
    msg_id = "msg-to-delete"
    gateway._client_webids[ws] = caller_webid
    gateway._local_rooms[room_id] = {"name": "T", "code": "x", "members": {ws}}
    gateway.clients.add(ws)
    gateway._store.add_room_member(room_id, caller_webid)
    gateway._store.add_federated_room_member(room_id, "did:key:zBob", "https://bob.example.com")
    # Save a message so sender check passes
    from datetime import datetime, timezone
    gateway._store.save_message(msg_id, room_id, "local_room", caller_webid, "Alice",
                                "hello", datetime.now(timezone.utc).isoformat())

    tasks = []
    with patch("asyncio.create_task", side_effect=lambda c: tasks.append(c) or MagicMock()):
        await gateway._handle_delete_local_message(ws, {
            "message_id": msg_id, "thread_id": room_id,
        })

    assert len(tasks) > 0


@pytest.mark.asyncio
async def test_edit_relays_to_federated_gateways(gateway):
    """_handle_edit_local_message schedules relay to federated gateways."""
    ws = _ws()
    room_id = "room-edt-1"
    caller_webid = "did:key:zAlice"
    msg_id = "msg-to-edit"
    gateway._client_webids[ws] = caller_webid
    gateway._local_rooms[room_id] = {"name": "T", "code": "x", "members": {ws}}
    gateway.clients.add(ws)
    gateway._store.add_room_member(room_id, caller_webid)
    gateway._store.add_federated_room_member(room_id, "did:key:zBob", "https://bob.example.com")
    from datetime import datetime, timezone
    gateway._store.save_message(msg_id, room_id, "local_room", caller_webid, "Alice",
                                "original", datetime.now(timezone.utc).isoformat())

    tasks = []
    with patch("asyncio.create_task", side_effect=lambda c: tasks.append(c) or MagicMock()):
        await gateway._handle_edit_local_message(ws, {
            "message_id": msg_id, "thread_id": room_id, "content": "edited!",
        })

    assert len(tasks) > 0


@pytest.mark.asyncio
async def test_room_delete_relay_delivers_to_local_members(gateway):
    """_handle_room_delete_relay delivers message_deleted to local members."""
    ws = _ws()
    room_id = "room-del-2"
    gateway._local_rooms[room_id] = {"name": "T", "members": {ws}}

    status, _ = await gateway._handle_room_delete_relay({
        "room_id": room_id, "message_id": "msg-del-remote",
    })

    assert status.startswith("200")
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "message_deleted"
    assert sent["message_id"] == "msg-del-remote"


@pytest.mark.asyncio
async def test_room_edit_relay_delivers_to_local_members(gateway):
    """_handle_room_edit_relay delivers message_edited to local members."""
    ws = _ws()
    room_id = "room-edt-2"
    gateway._local_rooms[room_id] = {"name": "T", "members": {ws}}

    status, _ = await gateway._handle_room_edit_relay({
        "room_id": room_id, "message_id": "msg-edt-remote",
        "new_content": "updated text", "edited_at": "2026-06-02T10:00:00Z",
        "from_webid": "did:key:zBob",
    })

    assert status.startswith("200")
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "message_edited"
    assert sent["new_content"] == "updated text"


@pytest.mark.asyncio
async def test_room_edit_relay_updates_store(gateway, tmp_path):
    """_handle_room_edit_relay updates the message content in the store."""
    from proxion_messenger_core.local_store import LocalStore
    from datetime import datetime, timezone
    store = LocalStore(str(tmp_path / "store2.db"))
    gateway._store = store
    room_id = "room-edt-3"
    msg_id = "msg-store-update"
    gateway._local_rooms[room_id] = {"name": "T", "members": set()}
    store.save_message(msg_id, room_id, "local_room", "did:key:zBob", "Bob",
                       "old content", datetime.now(timezone.utc).isoformat())

    await gateway._handle_room_edit_relay({
        "room_id": room_id, "message_id": msg_id,
        "new_content": "new content", "edited_at": "2026-06-02T10:00:00Z",
        "from_webid": "did:key:zBob",
    })

    msgs = store.get_messages(room_id)
    updated = next((m for m in msgs if m["message_id"] == msg_id), None)
    assert updated is not None
    assert updated["content"] == "new content"
