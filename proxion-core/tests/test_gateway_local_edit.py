"""Tests for edit_local_message command."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def gateway(tmp_db):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x00" * 32
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9996, db_path=tmp_db),
        read_state=ReadState(),
    )


@pytest.fixture
def two_clients(gateway):
    sender = MagicMock()
    sender.send = AsyncMock()
    target = MagicMock()
    target.send = AsyncMock()
    gateway.clients = {sender, target}
    gateway._client_webids[sender] = "did:key:alice"
    gateway._client_webids[target] = "did:key:bob"
    gateway._webid_sockets["did:key:alice"] = sender
    gateway._webid_sockets["did:key:bob"] = target
    return sender, target


@pytest.mark.asyncio
async def test_edit_local_message_updates_db(gateway, two_clients):
    sender, _ = two_clients
    gateway._store.save_message(
        "msg-edit-1", "did:key:bob", "dm",
        "did:key:alice", "Alice", "original text", "2026-04-16T10:00:00+00:00"
    )
    msgs_before = gateway._store.get_messages("did:key:bob")
    assert msgs_before[0]["content"] == "original text"

    await gateway.process_command(sender, {
        "cmd": "edit_local_message",
        "message_id": "msg-edit-1",
        "thread_id": "did:key:bob",
        "content": "edited text",
    })
    msgs_after = gateway._store.get_messages("did:key:bob")
    assert msgs_after[0]["content"] == "edited text"


@pytest.mark.asyncio
async def test_edit_local_message_broadcasts_event(gateway, two_clients):
    sender, target = two_clients
    await gateway.process_command(sender, {
        "cmd": "edit_local_message",
        "message_id": "msg-x",
        "thread_id": "some-dm-thread",
        "content": "new content",
    })
    all_calls = sender.send.call_args_list + target.send.call_args_list
    payloads = [json.loads(c[0][0]) for c in all_calls]
    edited = [p for p in payloads if p.get("type") == "message_edited"]
    assert len(edited) >= 1
    assert edited[0]["message_id"] == "msg-x"
    assert edited[0]["new_content"] == "new content"


@pytest.mark.asyncio
async def test_edit_local_message_in_room_broadcasts_to_members(gateway, two_clients):
    sender, target = two_clients
    gateway._local_rooms["room-edit"] = {
        "name": "EditRoom", "code": "xyz", "members": {sender, target},
        "invite_url": "", "history_mode": "none", "messages": [],
    }
    gateway._room_codes["xyz"] = "room-edit"

    await gateway.process_command(sender, {
        "cmd": "edit_local_message",
        "message_id": "msg-room-edit-1",
        "thread_id": "room-edit",
        "content": "edited room message",
    })
    all_calls = sender.send.call_args_list + target.send.call_args_list
    payloads = [json.loads(c[0][0]) for c in all_calls]
    edited = [p for p in payloads if p.get("type") == "message_edited"]
    assert len(edited) == 2


@pytest.mark.asyncio
async def test_edit_local_message_empty_content_ignored(gateway, two_clients):
    sender, _ = two_clients
    gateway._store.save_message(
        "msg-safe-edit", "thread-1", "dm",
        "did:key:alice", "Alice", "keep this", "2026-04-16T10:00:00+00:00"
    )
    # Empty content should not update the DB
    await gateway.process_command(sender, {
        "cmd": "edit_local_message",
        "message_id": "msg-safe-edit",
        "thread_id": "thread-1",
        "content": "",
    })
    msgs = gateway._store.get_messages("thread-1")
    assert msgs[0]["content"] == "keep this"
