"""Tests for delete_local_message command."""
import json
import pytest
import tempfile
import os
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
        config=GatewayConfig(port=9997, db_path=tmp_db),
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
async def test_delete_local_message_removes_from_db(gateway, two_clients):
    sender, target = two_clients
    # First store a message
    gateway._store.save_message(
        "msg-del-1", "did:key:bob", "dm",
        "did:key:alice", "Alice", "hello", "2026-04-15T10:00:00+00:00"
    )
    assert len(gateway._store.get_messages("did:key:bob")) == 1

    await gateway.process_command(sender, {
        "cmd": "delete_local_message",
        "message_id": "msg-del-1",
        "thread_id": "did:key:bob",
    })
    # Message should be gone from DB
    assert len(gateway._store.get_messages("did:key:bob")) == 0


@pytest.mark.asyncio
async def test_delete_local_message_broadcasts_event(gateway, two_clients):
    sender, target = two_clients
    await gateway.process_command(sender, {
        "cmd": "delete_local_message",
        "message_id": "msg-x",
        "thread_id": "some-dm-thread",
    })
    # Both sender and target should receive message_deleted event (DM path broadcasts to all)
    all_calls = sender.send.call_args_list + target.send.call_args_list
    payloads = [json.loads(c[0][0]) for c in all_calls]
    deleted_events = [p for p in payloads if p.get("type") == "message_deleted"]
    assert len(deleted_events) >= 1
    assert deleted_events[0]["message_id"] == "msg-x"


@pytest.mark.asyncio
async def test_delete_local_message_in_room_broadcasts_to_members(gateway, two_clients):
    sender, target = two_clients
    # Create a local room with both clients as members
    gateway._local_rooms["room-test"] = {
        "name": "Test", "code": "abc", "members": {sender, target},
        "invite_url": "", "history_mode": "none", "messages": [],
    }
    gateway._room_codes["abc"] = "room-test"

    await gateway.process_command(sender, {
        "cmd": "delete_local_message",
        "message_id": "msg-room-1",
        "thread_id": "room-test",
    })
    # Both room members should get the deletion
    all_calls = sender.send.call_args_list + target.send.call_args_list
    payloads = [json.loads(c[0][0]) for c in all_calls]
    deleted_events = [p for p in payloads if p.get("type") == "message_deleted"]
    assert len(deleted_events) == 2


@pytest.mark.asyncio
async def test_delete_nonexistent_message_is_safe(gateway, two_clients):
    sender, _ = two_clients
    # Should not raise even if message doesn't exist in DB
    await gateway.process_command(sender, {
        "cmd": "delete_local_message",
        "message_id": "does-not-exist",
        "thread_id": "some-thread",
    })
