"""Tests: GET /room-history/{room_id} REST endpoint."""
from __future__ import annotations
import asyncio
import json
import socket
import pytest
from datetime import datetime, timezone
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
async def running_gateway(tmp_path):
    key = Ed25519PrivateKey.generate()
    agent = AgentState(identity_key=key, store_key=None)
    http_port = _free_port()
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=_free_port(), http_port=http_port,
                             web_dir=None, db_path=str(tmp_path / "t.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "t.db"))
    # Seed a local room with a code and messages
    room_id = "room-rest-1"
    gw._local_rooms[room_id] = {"name": "Test", "code": "secret123", "members": set()}
    ts = datetime.now(timezone.utc).isoformat()
    for i in range(5):
        gw._store.save_message(f"m-{i}", room_id, "local_room",
                               "did:key:zAlice", "Alice", f"msg {i}", ts)
    server_task = asyncio.create_task(gw._serve_http(None, http_port))
    await asyncio.sleep(0.3)
    yield gw, http_port, room_id
    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_room_history_correct_code_returns_messages(running_gateway):
    gw, port, room_id = running_gateway
    async with httpx.AsyncClient() as client:
        r = await client.get(f"http://127.0.0.1:{port}/room-history/{room_id}?code=secret123")
    assert r.status_code == 200
    data = r.json()
    assert data["room_id"] == room_id
    assert len(data["messages"]) == 5


@pytest.mark.asyncio
async def test_room_history_wrong_code_forbidden(running_gateway):
    gw, port, room_id = running_gateway
    async with httpx.AsyncClient() as client:
        r = await client.get(f"http://127.0.0.1:{port}/room-history/{room_id}?code=wrong")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_room_history_limit_capped(running_gateway):
    gw, port, room_id = running_gateway
    # Add 250 messages; request limit=500 → capped at 200
    ts = datetime.now(timezone.utc).isoformat()
    for i in range(250):
        gw._store.save_message(f"big-{i}", room_id, "local_room",
                               "did:key:zAlice", "Alice", f"big {i}", ts)
    async with httpx.AsyncClient() as client:
        r = await client.get(f"http://127.0.0.1:{port}/room-history/{room_id}?code=secret123&limit=500")
    assert r.status_code == 200
    data = r.json()
    assert len(data["messages"]) <= 200
