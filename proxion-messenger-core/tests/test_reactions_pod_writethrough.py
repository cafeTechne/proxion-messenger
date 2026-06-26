"""Tests: local room reactions are synced to pod as room messages."""
from __future__ import annotations

import asyncio
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


def _mock_pod_client(gw):
    mock_client = MagicMock()
    mock_client.put = MagicMock(return_value=None)
    gw._pod_webid = "https://pod.example/profile/card#me"
    gw.dm_clients[gw._pod_webid] = (MagicMock(), mock_client)
    return mock_client


@pytest.mark.asyncio
async def test_reaction_triggers_room_message_sync(gateway):
    """add_reaction in a local room fires _sync_room_message_to_pod."""
    ws = _mock_ws()
    gateway.clients.add(ws)
    webid = "https://alice.pod/profile/card#me"
    gateway._client_webids[ws] = webid
    gateway._display_names[ws] = "Alice"

    room_id = "test-react-room"
    gateway._local_rooms[room_id] = {
        "members": {ws},
        "messages": [],
        "history_mode": "none",
    }
    gateway._store.save_room(room_id, "Test", "code1", "", "none", webid)
    gateway._store.save_message(
        "msg-target", room_id, "room", webid, "Alice", "Original message",
        "2024-01-01T00:00:00+00:00"
    )

    sync_calls = []

    async def fake_sync(room_id, msg):
        sync_calls.append((room_id, msg))

    gateway._sync_room_message_to_pod = fake_sync
    _mock_pod_client(gateway)

    mock_react_msg = MagicMock()
    mock_react_msg.message_id = "react-msg-id-1"
    mock_react_msg.to_dict.return_value = {"type": "reaction", "message_id": "react-msg-id-1", "emoji": "👍"}

    with patch("proxion_messenger_core.messaging.compose_reaction", return_value=mock_react_msg):
        await gateway.process_command(ws, {
            "cmd": "add_reaction",
            "room_id": room_id,
            "message_id": "msg-target",
            "emoji": "👍",
        })
        await asyncio.sleep(0)  # allow create_task to execute

    assert len(sync_calls) > 0


@pytest.mark.asyncio
async def test_reaction_pod_sync_skips_without_pod(gateway):
    """add_reaction works normally even when pod is not connected."""
    ws = _mock_ws()
    gateway.clients.add(ws)
    webid = "https://alice.pod/profile/card#me"
    gateway._client_webids[ws] = webid
    gateway._display_names[ws] = "Alice"

    room_id = "react-no-pod-room"
    gateway._local_rooms[room_id] = {
        "members": {ws},
        "messages": [],
        "history_mode": "none",
    }
    gateway._store.save_room(room_id, "Test", "code2", "", "none", webid)
    gateway._store.save_message(
        "msg-target2", room_id, "room", webid, "Alice", "Hello",
        "2024-01-01T00:00:00+00:00"
    )
    # Should not raise without pod
    await gateway.process_command(ws, {
        "cmd": "add_reaction",
        "room_id": room_id,
        "message_id": "msg-target2",
        "emoji": "❤️",
    })
