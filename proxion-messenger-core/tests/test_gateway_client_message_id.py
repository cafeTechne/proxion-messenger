"""Tests: gateway accepts client-provided message IDs for local room messages."""
from __future__ import annotations

import json
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState


@pytest.fixture
def agent():
    a = AgentState.generate()
    a.webid = "https://alice.pod/profile/card#me"
    return a


@pytest.fixture
def gateway(agent):
    gw = ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=GatewayConfig())
    return gw


def _mock_ws(webid="https://alice.pod/profile/card#me"):
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
async def test_client_message_id_accepted(gateway):
    """local_message with a valid UUID uses the client-provided message_id."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-abc"
    gateway._local_rooms[room_id] = {"members": {ws}, "messages": [], "history_mode": "none"}
    client_id = str(uuid.uuid4())
    await gateway.process_command(ws, {
        "cmd": "send_room",
        "room_id": room_id,
        "content": "hello",
        "message_id": client_id,
    })
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["message_id"] == client_id


@pytest.mark.asyncio
async def test_client_message_id_invalid_falls_back(gateway):
    """Non-UUID message_id is ignored; gateway generates its own."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-def"
    gateway._local_rooms[room_id] = {"members": {ws}, "messages": [], "history_mode": "none"}
    await gateway.process_command(ws, {
        "cmd": "send_room",
        "room_id": room_id,
        "content": "hi",
        "message_id": "not-a-uuid",
    })
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["message_id"] != "not-a-uuid"
    assert sent["message_id"].startswith("local-")


@pytest.mark.asyncio
async def test_client_message_id_in_broadcast(gateway):
    """Client-provided UUID appears in the broadcast event to all room members."""
    ws1 = _mock_ws()
    ws2 = _mock_ws("https://bob.pod/profile/card#me")
    await _register(gateway, ws1)
    await _register(gateway, ws2, "https://bob.pod/profile/card#me")
    room_id = "room-ghi"
    gateway._local_rooms[room_id] = {"members": {ws1, ws2}, "messages": [], "history_mode": "none"}
    client_id = str(uuid.uuid4())
    await gateway.process_command(ws1, {
        "cmd": "send_room",
        "room_id": room_id,
        "content": "broadcast test",
        "message_id": client_id,
    })
    # Both ws1 and ws2 should receive the message with client's ID
    for ws in (ws1, ws2):
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["message_id"] == client_id
