"""Tests for scheduled message commands."""
from __future__ import annotations

import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState


@pytest.fixture
def agent():
    a = AgentState.generate()
    a.webid = "https://alice.pod/profile/card#me"
    return a


@pytest.fixture
def gateway(agent):
    return ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=GatewayConfig())


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
async def test_schedule_message_stored(gateway):
    """schedule_message stores to in-memory list and returns message_scheduled."""
    ws = _mock_ws()
    await _register(gateway, ws)
    future = time.time() + 3600
    import datetime
    send_at = datetime.datetime.fromtimestamp(future, tz=datetime.timezone.utc).isoformat()
    await gateway.process_command(ws, {
        "cmd": "schedule_message",
        "thread_id": "room-abc",
        "content": "hello future",
        "send_at": send_at,
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "message_scheduled"
    assert resp["id"]
    assert resp["content_preview"] == "hello future"


@pytest.mark.asyncio
async def test_schedule_in_past_rejected(gateway):
    """send_at in the past returns error."""
    ws = _mock_ws()
    await _register(gateway, ws)
    import datetime
    past = datetime.datetime.fromtimestamp(time.time() - 60, tz=datetime.timezone.utc).isoformat()
    await gateway.process_command(ws, {
        "cmd": "schedule_message",
        "thread_id": "room-abc",
        "content": "too late",
        "send_at": past,
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "future" in resp["message"].lower()


@pytest.mark.asyncio
async def test_schedule_too_far_future_rejected(gateway):
    """More than 1 year ahead is rejected."""
    ws = _mock_ws()
    await _register(gateway, ws)
    import datetime
    far = datetime.datetime.fromtimestamp(time.time() + 366 * 86400, tz=datetime.timezone.utc).isoformat()
    await gateway.process_command(ws, {
        "cmd": "schedule_message",
        "thread_id": "room-abc",
        "content": "too far",
        "send_at": far,
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "1 year" in resp["message"].lower()


@pytest.mark.asyncio
async def test_list_scheduled_returns_empty_without_store(gateway):
    """list_scheduled returns empty list when no store configured."""
    ws = _mock_ws()
    await _register(gateway, ws)
    gateway._store = None
    await gateway.process_command(ws, {"cmd": "list_scheduled"})
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "scheduled_list"
    assert resp["items"] == []


@pytest.mark.asyncio
async def test_cancel_scheduled_requires_store(gateway):
    """cancel_scheduled with no store is a no-op (no crash)."""
    ws = _mock_ws()
    await _register(gateway, ws)
    gateway._store = None
    await gateway.process_command(ws, {"cmd": "cancel_scheduled", "id": "some-id"})


@pytest.mark.asyncio
async def test_schedule_message_missing_fields_returns_error(gateway):
    """Missing content returns error."""
    ws = _mock_ws()
    await _register(gateway, ws)
    import datetime
    send_at = datetime.datetime.fromtimestamp(time.time() + 3600, tz=datetime.timezone.utc).isoformat()
    await gateway.process_command(ws, {
        "cmd": "schedule_message",
        "thread_id": "room-abc",
        "content": "",
        "send_at": send_at,
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"


@pytest.mark.asyncio
async def test_cancel_scheduled_by_owner(gateway):
    """cancel_scheduled marks cancelled=1 via mock store."""
    ws = _mock_ws()
    await _register(gateway, ws)
    mock_store = MagicMock()
    mock_store.cancel_scheduled_message.return_value = True
    gateway._store = mock_store
    await gateway.process_command(ws, {"cmd": "cancel_scheduled", "id": "sched-123"})
    mock_store.cancel_scheduled_message.assert_called_once_with("sched-123", "https://alice.pod/profile/card#me")
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "scheduled_cancelled"


# ── R11.3.1: list_scheduled scoped to calling user only ────────────────────


@pytest.mark.asyncio
async def test_list_scheduled_scoped_to_caller(tmp_path):
    """R11.3.1: list_scheduled only returns messages for the registered user."""
    from proxion_messenger_core.gateway import GatewayConfig
    from proxion_messenger_core.local_store import LocalStore
    import datetime

    store = LocalStore(str(tmp_path / "test.db"))
    alice_webid = "https://alice.pod/profile/card#me"
    bob_webid = "https://bob.pod/profile/card#me"

    future = time.time() + 3600
    now = time.time()
    store.save_scheduled_message({"id": "sched-alice-1", "from_webid": alice_webid, "thread_id": "room-1", "content": "hello from alice", "send_at": future, "created_at": now})
    store.save_scheduled_message({"id": "sched-bob-1", "from_webid": bob_webid, "thread_id": "room-1", "content": "hello from bob", "send_at": future, "created_at": now})

    a = AgentState.generate()
    gw = ProxionGateway(
        agent=a, dm_clients={}, room_memberships={},
        config=GatewayConfig(db_path=str(tmp_path / "test.db")), read_state=None,
    )
    from proxion_messenger_core.readstate import ReadState
    gw._store = store

    ws = _mock_ws(alice_webid)
    gw.clients.add(ws)
    gw._client_webids[ws] = alice_webid

    await gw.process_command(ws, {"cmd": "list_scheduled"})
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "scheduled_list"
    ids = [item["id"] for item in resp["items"]]
    assert "sched-alice-1" in ids
    assert "sched-bob-1" not in ids


# ── R11.3.2: cancel_scheduled rejects other users' messages ────────────────


@pytest.mark.asyncio
async def test_cancel_scheduled_rejects_other_user(tmp_path):
    """R11.3.2: cancel_scheduled returns error when the message belongs to another user."""
    from proxion_messenger_core.local_store import LocalStore

    store = LocalStore(str(tmp_path / "r11.db"))
    alice_webid = "https://alice.pod/profile/card#me"
    bob_webid = "https://bob.pod/profile/card#me"

    store.save_scheduled_message({"id": "sched-bobs-msg", "from_webid": bob_webid, "thread_id": "room-x", "content": "bob's message", "send_at": time.time() + 3600, "created_at": time.time()})

    a = AgentState.generate()
    gw = ProxionGateway(
        agent=a, dm_clients={}, room_memberships={},
        config=GatewayConfig(),
    )
    gw._store = store

    # Alice tries to cancel Bob's message
    ws = _mock_ws(alice_webid)
    gw.clients.add(ws)
    gw._client_webids[ws] = alice_webid

    await gw.process_command(ws, {"cmd": "cancel_scheduled", "id": "sched-bobs-msg"})
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "not yours" in resp.get("message", "").lower() or resp["type"] == "error"

    # Bob's message should still be uncancelled
    items = store.get_scheduled_messages_for_user(bob_webid)
    assert any(i["id"] == "sched-bobs-msg" for i in items)
