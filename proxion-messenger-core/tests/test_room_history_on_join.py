"""Tests: room history snapshot sent on federated join (G5)."""
from __future__ import annotations
import json
from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock
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
async def test_history_sent_after_federated_join(gateway):
    """announce_room_join sends room_history when messages exist."""
    ws = _ws()
    room_id = "room-hist-1"
    code = "histcode1"
    caller_webid = "did:key:zBob"
    gateway._client_webids[ws] = caller_webid
    gateway._local_rooms[room_id] = {"name": "Test", "code": code, "members": {ws}}
    gateway.clients.add(ws)

    # Save some messages
    ts = datetime.now(timezone.utc).isoformat()
    gateway._store.add_room_member(room_id, caller_webid)
    gateway._store.save_message("msg-1", room_id, "local_room", "did:key:zAlice", "Alice", "Hello!", ts)
    gateway._store.save_message("msg-2", room_id, "local_room", "did:key:zAlice", "Alice", "World!", ts)

    from unittest.mock import patch
    with patch("proxion_messenger_core.relay._validate_relay_target", return_value=True):
        await gateway._handle_announce_room_join(ws, {
            "room_id": room_id,
            "code": code,
            "home_gateway": "https://bob.example.com",
        })

    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    history_events = [c for c in calls if c.get("type") == "room_history"]
    assert len(history_events) == 1
    assert history_events[0]["room_id"] == room_id
    assert len(history_events[0]["messages"]) == 2


@pytest.mark.asyncio
async def test_no_history_event_when_room_empty(gateway):
    """announce_room_join sends no room_history when the room has no messages."""
    ws = _ws()
    room_id = "room-hist-2"
    code = "histcode2"
    caller_webid = "did:key:zCarol"
    gateway._client_webids[ws] = caller_webid
    gateway._local_rooms[room_id] = {"name": "Empty", "code": code, "members": {ws}}
    gateway.clients.add(ws)
    gateway._store.add_room_member(room_id, caller_webid)

    from unittest.mock import patch
    with patch("proxion_messenger_core.relay._validate_relay_target", return_value=True):
        await gateway._handle_announce_room_join(ws, {
            "room_id": room_id,
            "code": code,
            "home_gateway": "https://carol.example.com",
        })

    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    history_events = [c for c in calls if c.get("type") == "room_history"]
    assert len(history_events) == 0


@pytest.mark.asyncio
async def test_history_capped_at_50_messages(gateway):
    """room_history snapshot is capped at 50 messages even if room has more."""
    ws = _ws()
    room_id = "room-hist-3"
    code = "histcode3"
    caller_webid = "did:key:zDave"
    gateway._client_webids[ws] = caller_webid
    gateway._local_rooms[room_id] = {"name": "Busy", "code": code, "members": {ws}}
    gateway.clients.add(ws)
    gateway._store.add_room_member(room_id, caller_webid)

    ts = datetime.now(timezone.utc).isoformat()
    for i in range(60):
        gateway._store.save_message(
            f"msg-{i}", room_id, "local_room",
            "did:key:zAlice", "Alice", f"Message {i}", ts,
        )

    from unittest.mock import patch
    with patch("proxion_messenger_core.relay._validate_relay_target", return_value=True):
        await gateway._handle_announce_room_join(ws, {
            "room_id": room_id,
            "code": code,
            "home_gateway": "https://dave.example.com",
        })

    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    history_events = [c for c in calls if c.get("type") == "room_history"]
    assert len(history_events) == 1
    assert len(history_events[0]["messages"]) <= 50
