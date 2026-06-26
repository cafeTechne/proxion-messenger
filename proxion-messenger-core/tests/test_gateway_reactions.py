"""Tests for add_reaction and remove_reaction gateway commands."""
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
        config=GatewayConfig(port=9990, db_path=str(tmp_path / "test.db")),
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
async def test_add_reaction_broadcast_to_room(gateway):
    """add_reaction in a room broadcasts reaction_added to all members."""
    ws_alice = _mock_ws()
    ws_bob = _mock_ws()
    await _register(gateway, ws_alice, "https://alice.pod/profile/card#me")
    await _register(gateway, ws_bob, "https://bob.pod/profile/card#me")

    room_id = "room-react-1"
    gateway._local_rooms[room_id] = {
        "members": {ws_alice, ws_bob},
        "messages": [],
        "history_mode": "none",
    }

    await gateway.process_command(ws_alice, {
        "cmd": "add_reaction",
        "room_id": room_id,
        "message_id": "msg-123",
        "emoji": "👍",
    })

    # Both alice and bob should receive the reaction_added event
    alice_calls = [json.loads(c[0][0]) for c in ws_alice.send.call_args_list]
    bob_calls = [json.loads(c[0][0]) for c in ws_bob.send.call_args_list]

    assert any(e.get("type") == "reaction_added" for e in alice_calls)
    assert any(e.get("type") == "reaction_added" for e in bob_calls)


@pytest.mark.asyncio
async def test_add_reaction_idempotent(gateway):
    """Adding the same emoji twice by the same user doesn't error."""
    ws_alice = _mock_ws()
    await _register(gateway, ws_alice, "https://alice.pod/profile/card#me")

    room_id = "room-react-2"
    gateway._local_rooms[room_id] = {
        "members": {ws_alice},
        "messages": [],
        "history_mode": "none",
    }

    # Add same reaction twice
    await gateway.process_command(ws_alice, {
        "cmd": "add_reaction",
        "room_id": room_id,
        "message_id": "msg-456",
        "emoji": "❤️",
    })
    await gateway.process_command(ws_alice, {
        "cmd": "add_reaction",
        "room_id": room_id,
        "message_id": "msg-456",
        "emoji": "❤️",
    })

    # No error should be raised
    calls = [json.loads(c[0][0]) for c in ws_alice.send.call_args_list]
    errors = [c for c in calls if c.get("type") == "error"]
    assert len(errors) == 0


@pytest.mark.asyncio
async def test_remove_reaction_broadcast_to_room(gateway):
    """remove_reaction broadcasts reaction_removed to all members."""
    ws_alice = _mock_ws()
    ws_bob = _mock_ws()
    await _register(gateway, ws_alice, "https://alice.pod/profile/card#me")
    await _register(gateway, ws_bob, "https://bob.pod/profile/card#me")

    room_id = "room-react-3"
    gateway._local_rooms[room_id] = {
        "members": {ws_alice, ws_bob},
        "messages": [],
        "history_mode": "none",
    }

    # First add a reaction
    await gateway.process_command(ws_alice, {
        "cmd": "add_reaction",
        "room_id": room_id,
        "message_id": "msg-789",
        "emoji": "😂",
    })

    # Clear previous calls
    ws_alice.send.reset_mock()
    ws_bob.send.reset_mock()

    # Then remove it
    await gateway.process_command(ws_alice, {
        "cmd": "remove_reaction",
        "room_id": room_id,
        "message_id": "msg-789",
        "emoji": "😂",
    })

    # Both alice and bob should receive the reaction_removed event
    alice_calls = [json.loads(c[0][0]) for c in ws_alice.send.call_args_list]
    bob_calls = [json.loads(c[0][0]) for c in ws_bob.send.call_args_list]

    assert any(e.get("type") == "reaction_removed" for e in alice_calls)
    assert any(e.get("type") == "reaction_removed" for e in bob_calls)


@pytest.mark.asyncio
async def test_remove_nonexistent_reaction_is_noop(gateway):
    """Removing an emoji not in the store doesn't error."""
    ws_alice = _mock_ws()
    await _register(gateway, ws_alice, "https://alice.pod/profile/card#me")

    room_id = "room-react-4"
    gateway._local_rooms[room_id] = {
        "members": {ws_alice},
        "messages": [],
        "history_mode": "none",
    }

    # Remove a reaction that was never added
    await gateway.process_command(ws_alice, {
        "cmd": "remove_reaction",
        "room_id": room_id,
        "message_id": "msg-999",
        "emoji": "🤷",
    })

    # No error should be raised
    calls = [json.loads(c[0][0]) for c in ws_alice.send.call_args_list]
    errors = [c for c in calls if c.get("type") == "error"]
    assert len(errors) == 0


@pytest.mark.asyncio
async def test_reaction_event_has_required_fields(gateway):
    """reaction_added event has: type, message_id, emoji, from_webid, thread_id."""
    ws_alice = _mock_ws()
    await _register(gateway, ws_alice, "https://alice.pod/profile/card#me")

    room_id = "room-react-5"
    gateway._local_rooms[room_id] = {
        "members": {ws_alice},
        "messages": [],
        "history_mode": "none",
    }

    await gateway.process_command(ws_alice, {
        "cmd": "add_reaction",
        "room_id": room_id,
        "message_id": "msg-fields",
        "emoji": "✨",
    })

    calls = [json.loads(c[0][0]) for c in ws_alice.send.call_args_list]
    reaction_event = next((e for e in calls if e.get("type") == "reaction_added"), None)

    assert reaction_event is not None
    assert reaction_event.get("type") == "reaction_added"
    assert reaction_event.get("message_id") == "msg-fields"
    assert reaction_event.get("emoji") == "✨"
    assert reaction_event.get("from_webid") == "https://alice.pod/profile/card#me"
    assert reaction_event.get("thread_id") == room_id


@pytest.mark.asyncio
async def test_remove_reaction_event_has_required_fields(gateway):
    """reaction_removed event has: type, message_id, emoji, from_webid."""
    ws_alice = _mock_ws()
    await _register(gateway, ws_alice, "https://alice.pod/profile/card#me")

    room_id = "room-react-6"
    gateway._local_rooms[room_id] = {
        "members": {ws_alice},
        "messages": [],
        "history_mode": "none",
    }

    # Add then remove
    await gateway.process_command(ws_alice, {
        "cmd": "add_reaction",
        "room_id": room_id,
        "message_id": "msg-remove-fields",
        "emoji": "🔥",
    })

    ws_alice.send.reset_mock()

    await gateway.process_command(ws_alice, {
        "cmd": "remove_reaction",
        "room_id": room_id,
        "message_id": "msg-remove-fields",
        "emoji": "🔥",
    })

    calls = [json.loads(c[0][0]) for c in ws_alice.send.call_args_list]
    removal_event = next((e for e in calls if e.get("type") == "reaction_removed"), None)

    assert removal_event is not None
    assert removal_event.get("type") == "reaction_removed"
    assert removal_event.get("message_id") == "msg-remove-fields"
    assert removal_event.get("emoji") == "🔥"
    assert removal_event.get("from_webid") == "https://alice.pod/profile/card#me"
