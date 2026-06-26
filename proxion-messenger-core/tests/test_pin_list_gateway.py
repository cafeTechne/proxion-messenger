"""Tests: list_pins command returns correct pins for a room."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x00" * 32
    agent.identity_key = MagicMock()
    gw = ProxionGateway(
        agent=agent,
        dm_clients={},
        room_memberships={},
        config=GatewayConfig(port=9990, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "test.db"))
    return gw


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    return ws


@pytest.mark.asyncio
async def test_list_pins_returns_room_pins(gateway):
    """get_pins command sends pins list for a room."""
    ws = _mock_ws()
    gateway.clients.add(ws)
    webid = "https://alice.pod/profile/card#me"
    gateway._client_webids[ws] = webid
    gateway._display_names[ws] = "Alice"

    room_id = "pinlist-room-1"
    gateway._local_rooms[room_id] = {
        "members": {ws},
        "messages": [],
        "history_mode": "none",
    }
    gateway._store.save_room(room_id, "Test Room", "code-pl", "", "none", webid)
    gateway._store.save_message(
        "msg-to-pin", room_id, "room", webid, "Alice", "Pin me", "2024-01-01T00:00:00+00:00"
    )
    gateway._store.save_pin(room_id, "msg-to-pin", webid, "Pin me")

    await gateway.process_command(ws, {"cmd": "get_pins", "thread_id": room_id})

    ws.send.assert_called()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "pins"
    assert len(sent["pins"]) >= 1
    assert any(p["message_id"] == "msg-to-pin" for p in sent["pins"])


@pytest.mark.asyncio
async def test_list_pins_returns_empty_for_no_pins(gateway):
    """get_pins returns empty list when no messages are pinned."""
    ws = _mock_ws()
    gateway.clients.add(ws)
    webid = "https://alice.pod/profile/card#me"
    gateway._client_webids[ws] = webid
    gateway._display_names[ws] = "Alice"

    room_id = "pinlist-room-2"
    gateway._local_rooms[room_id] = {"members": {ws}, "messages": [], "history_mode": "none"}
    gateway._store.save_room(room_id, "Empty Room", "code-ep", "", "none", webid)

    await gateway.process_command(ws, {"cmd": "get_pins", "thread_id": room_id})
    ws.send.assert_called()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "pins"
    assert sent["pins"] == []


@pytest.mark.asyncio
async def test_pin_and_unpin_roundtrip(gateway):
    """pin_message then unpin_message results in no pins."""
    ws = _mock_ws()
    gateway.clients.add(ws)
    webid = "https://alice.pod/profile/card#me"
    gateway._client_webids[ws] = webid
    gateway._display_names[ws] = "Alice"

    room_id = "pinlist-room-3"
    gateway._local_rooms[room_id] = {"members": {ws}, "messages": [], "history_mode": "none"}
    gateway._store.save_room(room_id, "Round Trip", "code-rt", "", "none", webid)
    gateway._store.save_message(
        "msg-roundtrip", room_id, "room", webid, "Alice", "Hello", "2024-01-01T00:00:00+00:00"
    )

    await gateway.process_command(ws, {
        "cmd": "pin_message",
        "message_id": "msg-roundtrip",
        "thread_id": room_id,
    })
    await gateway.process_command(ws, {
        "cmd": "unpin_message",
        "message_id": "msg-roundtrip",
        "thread_id": room_id,
    })
    pins = gateway._store.get_pins(room_id)
    assert not any(p["message_id"] == "msg-roundtrip" for p in pins)
