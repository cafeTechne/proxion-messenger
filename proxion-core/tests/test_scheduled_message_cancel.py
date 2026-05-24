"""Tests: cancel_scheduled_message removes item; not delivered after cancel."""
from __future__ import annotations

import json
import time
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
async def test_cancel_scheduled_message_removes_from_store(gateway):
    """cancel_scheduled removes the message from SQLite."""
    ws = _mock_ws()
    gateway.clients.add(ws)
    webid = "https://alice.pod/profile/card#me"
    gateway._client_webids[ws] = webid
    gateway._display_names[ws] = "Alice"

    send_at = time.time() + 3600
    gateway._store.save_scheduled_message({"id": "job-cancel-1", "thread_id": "thread-1", "from_webid": webid, "content": "Will cancel", "send_at": send_at, "created_at": send_at})

    await gateway.process_command(ws, {"cmd": "cancel_scheduled", "id": "job-cancel-1"})

    due = gateway._store.get_due_scheduled_messages(time.time() + 7200)
    assert not any(m.get("job_id") == "job-cancel-1" for m in due)


@pytest.mark.asyncio
async def test_cancel_sends_confirmation(gateway):
    """cancel_scheduled sends scheduled_cancelled event."""
    ws = _mock_ws()
    gateway.clients.add(ws)
    webid = "https://alice.pod/profile/card#me"
    gateway._client_webids[ws] = webid
    gateway._display_names[ws] = "Alice"

    send_at = time.time() + 3600
    gateway._store.save_scheduled_message({"id": "job-cancel-2", "thread_id": "thread-2", "from_webid": webid, "content": "Also cancel", "send_at": send_at, "created_at": send_at})

    await gateway.process_command(ws, {"cmd": "cancel_scheduled", "id": "job-cancel-2"})
    ws.send.assert_called()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("type") in ("scheduled_cancelled", "cancel_scheduled_ack", "info")


@pytest.mark.asyncio
async def test_schedule_then_cancel_not_delivered(gateway):
    """A scheduled message that is cancelled is never delivered by the scheduler."""
    ws = _mock_ws()
    gateway.clients.add(ws)
    webid = "https://alice.pod/profile/card#me"
    gateway._client_webids[ws] = webid

    room_id = "room-sched"
    gateway._local_rooms[room_id] = {"members": {ws}, "messages": [], "history_mode": "none"}
    gateway._store.save_room(room_id, "Sched Room", "code-sc", "", "none", webid)

    past_time = time.time() - 60  # Due now
    gateway._store.save_scheduled_message(
        {"id": "job-del-3", "thread_id": room_id, "from_webid": webid, "content": "Should not deliver", "send_at": past_time, "created_at": past_time}
    )
    gateway._store.cancel_scheduled_message("job-del-3", webid)

    # Verify not in due list
    due = gateway._store.get_due_scheduled_messages(time.time())
    assert not any(m.get("job_id") == "job-del-3" for m in due)
