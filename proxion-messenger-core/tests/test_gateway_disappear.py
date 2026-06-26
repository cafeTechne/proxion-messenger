"""Tests for disappearing message timer commands."""
from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def agent():
    a = AgentState.generate()
    a.webid = "https://alice.pod/profile/card#me"
    return a


@pytest.fixture
def gateway(agent):
    return ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=GatewayConfig(), read_state=ReadState())


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
async def test_set_disappear_timer_broadcasts(gateway):
    """set_disappear_timer broadcasts disappear_timer_updated to all room members."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-dis"
    gateway._local_rooms[room_id] = {"creator_webid": "https://alice.pod/profile/card#me", "members": {ws}, "messages": [], "history_mode": "none"}
    await gateway.process_command(ws, {
        "cmd": "set_disappear_timer",
        "room_id": room_id,
        "ms": 30000,
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "disappear_timer_updated"
    assert resp["ms"] == 30000
    assert resp["room_id"] == room_id


@pytest.mark.asyncio
async def test_set_disappear_timer_stored_in_memory(gateway):
    """Timer value is stored in _room_disappear_timers."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-mem"
    gateway._local_rooms[room_id] = {"creator_webid": "https://alice.pod/profile/card#me", "members": {ws}, "messages": [], "history_mode": "none"}
    await gateway.process_command(ws, {
        "cmd": "set_disappear_timer",
        "room_id": room_id,
        "ms": 60000,
    })
    assert gateway._room_disappear_timers.get(room_id) == 60000


@pytest.mark.asyncio
async def test_set_disappear_timer_zero_disables(gateway):
    """ms=0 disables the timer."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-zero"
    gateway._local_rooms[room_id] = {"creator_webid": "https://alice.pod/profile/card#me", "members": {ws}, "messages": [], "history_mode": "none"}
    gateway._room_disappear_timers[room_id] = 30000
    await gateway.process_command(ws, {
        "cmd": "set_disappear_timer",
        "room_id": room_id,
        "ms": 0,
    })
    assert gateway._room_disappear_timers.get(room_id) == 0


@pytest.mark.asyncio
async def test_set_disappear_timer_not_member_rejected(gateway):
    """Non-member cannot set the timer."""
    ws = _mock_ws()
    await _register(gateway, ws)
    ws2 = _mock_ws()
    room_id = "room-nm"
    gateway._local_rooms[room_id] = {"members": {ws2}, "messages": [], "history_mode": "none"}
    await gateway.process_command(ws, {
        "cmd": "set_disappear_timer",
        "room_id": room_id,
        "ms": 30000,
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"


@pytest.mark.asyncio
async def test_get_disappear_timer_returns_current(gateway):
    """get_disappear_timer returns the current timer for a room."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-get"
    gateway._local_rooms[room_id] = {"members": {ws}, "messages": [], "history_mode": "none"}
    gateway._room_disappear_timers[room_id] = 45000
    await gateway.process_command(ws, {
        "cmd": "get_disappear_timer",
        "room_id": room_id,
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "disappear_timer"
    assert resp["ms"] == 45000


@pytest.mark.asyncio
async def test_set_disappear_timer_persists_to_store(gateway):
    """set_disappear_timer calls store.set_room_disappear_timer."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-store"
    gateway._local_rooms[room_id] = {"creator_webid": "https://alice.pod/profile/card#me", "members": {ws}, "messages": [], "history_mode": "none"}
    mock_store = MagicMock()
    gateway._store = mock_store
    await gateway.process_command(ws, {
        "cmd": "set_disappear_timer",
        "room_id": room_id,
        "ms": 120000,
    })
    mock_store.set_room_disappear_timer.assert_called_once_with(room_id, 120000)


# ── R11.2.1: disappear timer fires and sends message_deleted ───────────────


@pytest.mark.asyncio
async def test_expire_loop_fires_message_deleted(gateway):
    """R11.2.1: _expire_messages_loop sends message_deleted for messages older than timer."""
    from datetime import datetime, timezone, timedelta

    ws = _mock_ws()
    await _register(gateway, ws)

    room_id = "room-expire-fires"
    # Create a room with a 500ms disappear timer
    gateway._local_rooms[room_id] = {
        "name": "r", "code": "c", "members": {ws}, "messages": [], "history_mode": "none"
    }
    gateway._room_disappear_timers[room_id] = 500  # 500ms

    # Add a message with a timestamp 2 seconds ago (older than 500ms timer)
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    gateway._local_rooms[room_id]["messages"].append({
        "message_id": "old-msg-expire",
        "content": "expiring soon",
        "timestamp": old_ts,
    })

    sleep_count = 0

    async def fake_sleep(_):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 2:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=fake_sleep):
        try:
            await gateway._expire_messages_loop()
        except asyncio.CancelledError:
            pass

    # The message should have been deleted from room state
    remaining_ids = [m["message_id"] for m in gateway._local_rooms[room_id]["messages"]]
    assert "old-msg-expire" not in remaining_ids

    # message_deleted event should have been sent to the room member
    events = [json.loads(call[0][0]) for call in ws.send.call_args_list]
    deleted_events = [e for e in events if e.get("type") == "message_deleted"]
    assert len(deleted_events) == 1
    assert deleted_events[0]["message_id"] == "old-msg-expire"


@pytest.mark.asyncio
async def test_expire_loop_does_not_delete_recent_messages(gateway):
    """Messages younger than the timer are NOT deleted."""
    from datetime import datetime, timezone

    ws = _mock_ws()
    await _register(gateway, ws)

    room_id = "room-keep-recent"
    gateway._local_rooms[room_id] = {
        "name": "r", "code": "c", "members": {ws}, "messages": [], "history_mode": "none"
    }
    gateway._room_disappear_timers[room_id] = 60000  # 1 minute

    # Add a brand-new message (should NOT be deleted)
    now_ts = datetime.now(timezone.utc).isoformat()
    gateway._local_rooms[room_id]["messages"].append({
        "message_id": "new-msg-keep",
        "content": "fresh",
        "timestamp": now_ts,
    })

    sleep_count = 0

    async def fake_sleep(_):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 2:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=fake_sleep):
        try:
            await gateway._expire_messages_loop()
        except asyncio.CancelledError:
            pass

    remaining_ids = [m["message_id"] for m in gateway._local_rooms[room_id]["messages"]]
    assert "new-msg-keep" in remaining_ids


# ── R11.2.2: late-join member doesn't see expired messages ─────────────────


@pytest.mark.asyncio
async def test_late_join_member_misses_expired_message(gateway):
    """R11.2.2: after expire loop fires, room['messages'] no longer contains the expired message."""
    from datetime import datetime, timezone, timedelta

    ws = _mock_ws()
    await _register(gateway, ws)

    room_id = "room-late-join"
    gateway._local_rooms[room_id] = {
        "name": "r", "code": "c", "members": {ws}, "messages": [], "history_mode": "none"
    }
    gateway._room_disappear_timers[room_id] = 500  # 500ms

    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    gateway._local_rooms[room_id]["messages"].append({
        "message_id": "expired-msg",
        "content": "gone",
        "timestamp": old_ts,
    })

    sleep_count = 0

    async def fake_sleep(_):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 2:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=fake_sleep):
        try:
            await gateway._expire_messages_loop()
        except asyncio.CancelledError:
            pass

    # Simulate a late-joining member: they would see room["messages"] as history
    remaining = [m["message_id"] for m in gateway._local_rooms[room_id]["messages"]]
    assert "expired-msg" not in remaining


# ── R11.2.3: disappear timers rebuilt from SQLite on restart ───────────────


@pytest.mark.asyncio
async def test_disappear_timers_rebuilt_from_sqlite_on_restart(tmp_path):
    """R11.2.3: a new gateway instance loads _room_disappear_timers from SQLite."""
    from proxion_messenger_core.gateway import GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState

    db_path = str(tmp_path / "disappear.db")

    # Session 1: create gateway, create room, set disappear timer
    agent1 = AgentState.generate()
    gw1 = ProxionGateway(
        agent=agent1, dm_clients={}, room_memberships={},
        config=GatewayConfig(db_path=db_path), read_state=ReadState(),
    )
    ws = _mock_ws()
    await _register(gw1, ws)
    await gw1.process_command(ws, {
        "cmd": "chat_room_create",
        "name": "Disappear Room",
    })
    # Get the room_id from the response
    ev = json.loads(ws.send.call_args_list[-1][0][0])
    room_id = ev.get("room_id", "")
    assert room_id

    # Set the disappear timer
    await gw1.process_command(ws, {
        "cmd": "set_disappear_timer",
        "room_id": room_id,
        "ms": 30000,
    })
    assert gw1._room_disappear_timers.get(room_id) == 30000

    # Session 2: fresh gateway at same DB
    agent2 = AgentState.generate()
    gw2 = ProxionGateway(
        agent=agent2, dm_clients={}, room_memberships={},
        config=GatewayConfig(db_path=db_path), read_state=ReadState(),
    )

    # The timer should have been restored from SQLite
    assert gw2._room_disappear_timers.get(room_id) == 30000
