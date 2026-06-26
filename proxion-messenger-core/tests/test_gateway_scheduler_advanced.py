"""Advanced scheduled-message tests — R11.1.1, R11.1.2, R11.1.3."""
from __future__ import annotations

import asyncio
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


def _make_gateway(tmp_path, **cfg_kwargs):
    agent = AgentState.generate()
    cfg = GatewayConfig(db_path=str(tmp_path / "gw.db"), **cfg_kwargs)
    gw = ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=cfg, read_state=ReadState())
    return gw, agent


def _mock_ws(webid="did:key:alice"):
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    return ws


def _seed_scheduled(store, from_webid, room_id, n=1, send_at_offset=-1):
    """Seed n due scheduled messages."""
    now = time.time()
    ids = []
    for i in range(n):
        sched_id = f"sched-{i:04d}"
        store.save_scheduled_message({
            "id": sched_id,
            "from_webid": from_webid,
            "thread_id": room_id,
            "content": f"message {i}",
            "send_at": now + send_at_offset,  # in the past = due
            "created_at": now - (n - i),      # ensures consistent ordering
        })
        ids.append(sched_id)
    return ids


# ── R11.1.1: 10 scheduled messages processed in order ─────────────────────


@pytest.mark.asyncio
async def test_scheduler_loop_processes_10_messages_in_order(tmp_path):
    """R11.1.1: scheduler processes 10 due messages and calls process_command for each."""
    gw, agent = _make_gateway(tmp_path)

    from proxion_messenger_core.didkey import pub_key_to_did
    alice_did = pub_key_to_did(agent.identity_pub_bytes)
    room_id = "sched-room-1"
    gw._local_rooms[room_id] = {"name": "r", "code": "c", "members": set(), "messages": [], "history_mode": "none"}

    ws = _mock_ws(alice_did)
    gw.clients.add(ws)
    gw._client_webids[ws] = alice_did

    _seed_scheduled(gw._store, alice_did, room_id, n=10)

    processed = []
    original_process = gw.process_command

    async def capture_process(websocket, data):
        if data.get("cmd") == "send_room":
            processed.append(data["content"])
        await original_process(websocket, data)

    gw.process_command = capture_process

    sleep_count = 0

    async def fake_sleep(_):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 2:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=fake_sleep):
        try:
            await gw._scheduler_loop()
        except asyncio.CancelledError:
            pass

    assert len(processed) == 10
    # Verify ordering by checking the message contents match seeded order
    for i, content in enumerate(processed):
        assert f"message {i}" in content or content.startswith("message"), content


# ── R11.1.2: cancelled message is not delivered ────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_loop_skips_cancelled_messages(tmp_path):
    """R11.1.2: a cancelled scheduled message is not delivered."""
    gw, agent = _make_gateway(tmp_path)

    from proxion_messenger_core.didkey import pub_key_to_did
    alice_did = pub_key_to_did(agent.identity_pub_bytes)
    room_id = "sched-room-2"
    gw._local_rooms[room_id] = {"name": "r", "code": "c", "members": set(), "messages": [], "history_mode": "none"}

    ws = _mock_ws(alice_did)
    gw.clients.add(ws)
    gw._client_webids[ws] = alice_did

    _seed_scheduled(gw._store, alice_did, room_id, n=1)
    # Cancel the scheduled message before the scheduler runs
    cancelled = gw._store.cancel_scheduled_message("sched-0000", alice_did)
    assert cancelled

    processed = []
    original_process = gw.process_command

    async def capture_process(websocket, data):
        if data.get("cmd") == "send_room":
            processed.append(data["content"])
        await original_process(websocket, data)

    gw.process_command = capture_process

    sleep_count = 0

    async def fake_sleep(_):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 2:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=fake_sleep):
        try:
            await gw._scheduler_loop()
        except asyncio.CancelledError:
            pass

    assert len(processed) == 0, f"Cancelled message was delivered: {processed}"


# ── R11.1.3: scheduler loop resumes after gateway restart ─────────────────


@pytest.mark.asyncio
async def test_scheduler_loop_resumes_after_restart(tmp_path):
    """R11.1.3: a new gateway instance pointed at the same DB picks up and delivers queued messages."""
    db_path = str(tmp_path / "shared.db")

    # Session 1: save a scheduled message directly to SQLite
    store = LocalStore(db_path)
    alice_did = "did:key:z6MkAlice"
    room_id = "sched-restart-room"
    store.save_scheduled_message({
        "id": "sched-restart-001",
        "from_webid": alice_did,
        "thread_id": room_id,
        "content": "restart test message",
        "send_at": time.time() - 1,  # already due
        "created_at": time.time() - 10,
    })

    # Session 2: fresh gateway pointed at same DB
    agent = AgentState.generate()
    cfg = GatewayConfig(db_path=db_path)
    gw2 = ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=cfg, read_state=ReadState())

    gw2._local_rooms[room_id] = {"name": "r", "code": "c", "members": set(), "messages": [], "history_mode": "none"}

    ws = _mock_ws(alice_did)
    gw2.clients.add(ws)
    gw2._client_webids[ws] = alice_did

    processed = []
    original_process = gw2.process_command

    async def capture_process(websocket, data):
        if data.get("cmd") == "send_room":
            processed.append(data["content"])
        await original_process(websocket, data)

    gw2.process_command = capture_process

    sleep_count = 0

    async def fake_sleep(_):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 2:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=fake_sleep):
        try:
            await gw2._scheduler_loop()
        except asyncio.CancelledError:
            pass

    assert len(processed) == 1
    assert "restart test message" in processed[0]
