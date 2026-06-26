"""Tests for reply_to_id field in gateway message broadcasts."""
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
        config=GatewayConfig(port=9992, db_path=str(tmp_path / "test.db")),
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
async def test_reply_to_id_included_in_room_broadcast(gateway):
    """Sending a room message with reply_to_id results in reply_to_id in broadcast."""
    ws_alice = _mock_ws()
    ws_bob = _mock_ws()
    await _register(gateway, ws_alice, "https://alice.pod/profile/card#me")
    await _register(gateway, ws_bob, "https://bob.pod/profile/card#me")

    room_id = "room-reply-1"
    gateway._local_rooms[room_id] = {
        "members": {ws_alice, ws_bob},
        "messages": [],
        "history_mode": "none",
    }

    await gateway.process_command(ws_alice, {
        "cmd": "send_room",
        "room_id": room_id,
        "content": "This is a reply",
        "reply_to_id": "msg-original-123",
    })

    # Bob should receive the message with reply_to_id
    bob_calls = [json.loads(c[0][0]) for c in ws_bob.send.call_args_list]
    message_event = next((e for e in bob_calls if e.get("type") == "message"), None)

    assert message_event is not None
    assert message_event.get("reply_to_id") == "msg-original-123"


@pytest.mark.asyncio
async def test_reply_to_id_none_when_not_set(gateway):
    """Sending without reply_to_id results in reply_to_id being absent or None."""
    ws_alice = _mock_ws()
    ws_bob = _mock_ws()
    await _register(gateway, ws_alice, "https://alice.pod/profile/card#me")
    await _register(gateway, ws_bob, "https://bob.pod/profile/card#me")

    room_id = "room-reply-2"
    gateway._local_rooms[room_id] = {
        "members": {ws_alice, ws_bob},
        "messages": [],
        "history_mode": "none",
    }

    await gateway.process_command(ws_alice, {
        "cmd": "send_room",
        "room_id": room_id,
        "content": "This is a standalone message",
    })

    # Bob should receive the message with reply_to_id as None
    bob_calls = [json.loads(c[0][0]) for c in ws_bob.send.call_args_list]
    message_event = next((e for e in bob_calls if e.get("type") == "message"), None)

    assert message_event is not None
    # reply_to_id should be None or absent
    assert message_event.get("reply_to_id") is None


@pytest.mark.asyncio
async def test_reply_to_id_included_in_dm_broadcast(gateway):
    """Sending a local_dm with reply_to_id includes it in broadcast to peer."""
    ws_alice = _mock_ws()
    ws_bob = _mock_ws()
    await _register(gateway, ws_alice, "https://alice.pod/profile/card#me")
    await _register(gateway, ws_bob, "https://bob.pod/profile/card#me")

    # Register the webid->socket mapping so _any_socket can find Bob
    gateway._webid_sockets["https://bob.pod/profile/card#me"] = ws_bob

    await gateway.process_command(ws_alice, {
        "cmd": "local_dm",
        "target_webid": "https://bob.pod/profile/card#me",
        "content": "Reply in DM",
        "reply_to_id": "msg-prev-dm-456",
    })

    # Bob should receive the DM with reply_to_id
    bob_calls = [json.loads(c[0][0]) for c in ws_bob.send.call_args_list]
    dm_event = next((e for e in bob_calls if e.get("type") == "message"), None)

    assert dm_event is not None
    assert dm_event.get("reply_to_id") == "msg-prev-dm-456"


@pytest.mark.asyncio
async def test_get_message_returns_stored_message(gateway):
    """get_message command returns the stored message dict with correct message_id."""
    ws_alice = _mock_ws()
    await _register(gateway, ws_alice, "https://alice.pod/profile/card#me")

    # Manually save a message via the store
    if not gateway._store:
        pytest.skip("Store not initialized")

    message_id = "msg-stored-789"
    gateway._store.save_message(
        message_id,
        "room-test",
        "room",
        "https://alice.pod/profile/card#me",
        "Alice",
        "Hello, world!",
        "2026-05-01T10:00:00+00:00",
    )

    await gateway.process_command(ws_alice, {
        "cmd": "get_message",
        "message_id": message_id,
    })

    calls = [json.loads(c[0][0]) for c in ws_alice.send.call_args_list]
    response = next((e for e in calls if e.get("type") == "message_fetched"), None)

    assert response is not None
    assert response.get("message") is not None
    # The returned message is a dict with message_id as a key
    message = response.get("message")
    assert message["message_id"] == message_id or message.get("message_id") == message_id


@pytest.mark.asyncio
async def test_get_message_returns_error_for_unknown_id(gateway):
    """get_message with unknown message_id returns a response with no message."""
    ws_alice = _mock_ws()
    await _register(gateway, ws_alice, "https://alice.pod/profile/card#me")

    await gateway.process_command(ws_alice, {
        "cmd": "get_message",
        "message_id": "nonexistent-msg-id",
    })

    calls = [json.loads(c[0][0]) for c in ws_alice.send.call_args_list]
    response = next((e for e in calls if e.get("type") == "message_fetched"), None)

    assert response is not None
    # For unknown message, the response should have message as None
    assert response.get("message") is None
    assert response.get("message_id") == "nonexistent-msg-id"
