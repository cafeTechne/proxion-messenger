"""Tests for mark_read gateway command."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x00" * 32
    agent.identity_key = MagicMock()
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    return ws


async def _register(gw, ws, webid="https://alice.pod/profile/card#me"):
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    gw._display_names[ws] = "Alice"


@pytest.mark.asyncio
async def test_mark_read_no_error(gateway):
    """mark_read on a valid DM thread sends no error response."""
    ws_alice = _mock_ws()
    await _register(gateway, ws_alice, "https://alice.pod/profile/card#me")

    await gateway.process_command(ws_alice, {
        "cmd": "mark_read",
        "thread_id": "some-dm-thread",
        "message_id": "msg-123",
    })

    # Check no error was sent
    calls = [json.loads(c[0][0]) for c in ws_alice.send.call_args_list]
    errors = [c for c in calls if c.get("type") == "error"]
    assert len(errors) == 0


@pytest.mark.asyncio
async def test_mark_read_broadcasts_read_receipt_to_room_members(gateway):
    """mark_read in a room sends read_receipt to other members (not to sender)."""
    ws_alice = _mock_ws()
    ws_bob = _mock_ws()
    await _register(gateway, ws_alice, "https://alice.pod/profile/card#me")
    await _register(gateway, ws_bob, "https://bob.pod/profile/card#me")

    room_id = "room-read-1"
    gateway._local_rooms[room_id] = {
        "members": {ws_alice, ws_bob},
        "messages": [],
        "history_mode": "none",
    }

    await gateway.process_command(ws_alice, {
        "cmd": "mark_read",
        "thread_id": room_id,
        "message_id": "msg-read-1",
    })

    # Bob should receive the read_receipt
    bob_calls = [json.loads(c[0][0]) for c in ws_bob.send.call_args_list]
    assert any(e.get("type") == "read_receipt" for e in bob_calls)


@pytest.mark.asyncio
async def test_mark_read_no_self_receipt(gateway):
    """The sender does not receive their own read_receipt."""
    ws_alice = _mock_ws()
    ws_bob = _mock_ws()
    await _register(gateway, ws_alice, "https://alice.pod/profile/card#me")
    await _register(gateway, ws_bob, "https://bob.pod/profile/card#me")

    room_id = "room-read-2"
    gateway._local_rooms[room_id] = {
        "members": {ws_alice, ws_bob},
        "messages": [],
        "history_mode": "none",
    }

    await gateway.process_command(ws_alice, {
        "cmd": "mark_read",
        "thread_id": room_id,
        "message_id": "msg-read-2",
    })

    # Alice should NOT receive a read_receipt about her own read
    alice_calls = [json.loads(c[0][0]) for c in ws_alice.send.call_args_list]
    read_receipts = [c for c in alice_calls if c.get("type") == "read_receipt"]
    assert len(read_receipts) == 0


@pytest.mark.asyncio
async def test_read_receipt_has_required_fields(gateway):
    """read_receipt event includes: type, thread_id, webid, message_id."""
    ws_alice = _mock_ws()
    ws_bob = _mock_ws()
    await _register(gateway, ws_alice, "https://alice.pod/profile/card#me")
    await _register(gateway, ws_bob, "https://bob.pod/profile/card#me")

    room_id = "room-read-3"
    gateway._local_rooms[room_id] = {
        "members": {ws_alice, ws_bob},
        "messages": [],
        "history_mode": "none",
    }

    await gateway.process_command(ws_alice, {
        "cmd": "mark_read",
        "thread_id": room_id,
        "message_id": "msg-read-3",
    })

    bob_calls = [json.loads(c[0][0]) for c in ws_bob.send.call_args_list]
    receipt = next((e for e in bob_calls if e.get("type") == "read_receipt"), None)

    assert receipt is not None
    assert receipt.get("type") == "read_receipt"
    assert receipt.get("thread_id") == room_id
    assert receipt.get("webid") == "https://alice.pod/profile/card#me"
    assert receipt.get("message_id") == "msg-read-3"


@pytest.mark.asyncio
async def test_mark_read_updates_store(gateway):
    """mark_read calls store.set_last_read (via mock store)."""
    ws_alice = _mock_ws()
    await _register(gateway, ws_alice, "https://alice.pod/profile/card#me")

    # Setup mock store
    mock_store = MagicMock()
    gateway._store = mock_store

    room_id = "room-read-4"
    gateway._local_rooms[room_id] = {
        "members": {ws_alice},
        "messages": [],
        "history_mode": "none",
    }

    await gateway.process_command(ws_alice, {
        "cmd": "mark_read",
        "thread_id": room_id,
        "message_id": "msg-read-4",
    })

    # Verify set_last_read was called with correct args
    mock_store.set_last_read.assert_called_once_with(
        "https://alice.pod/profile/card#me",
        room_id,
    )


@pytest.mark.asyncio
async def test_mark_read_unregistered_returns_error(gateway):
    """Unregistered websocket calling mark_read gets error response."""
    ws_unregistered = _mock_ws()
    # Do NOT register this websocket

    # Should not crash
    await gateway.process_command(ws_unregistered, {
        "cmd": "mark_read",
        "thread_id": "some-thread",
        "message_id": "msg-unreg",
    })

    # Unregistered client should get error response
    assert ws_unregistered.send.call_count == 1
    msg = json.loads(ws_unregistered.send.call_args[0][0])
    assert msg["type"] == "error"
    assert msg["message"] == "Not registered"
