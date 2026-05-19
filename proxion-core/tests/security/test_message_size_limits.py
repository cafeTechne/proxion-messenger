"""Round 8: Message content size limits and empty-content rejection."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway(tmp_path):
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9975, db_path=str(tmp_path / "sizelim.db")),
        read_state=ReadState(),
    )
    return gw


def _registered_ws(gw, webid="did:key:size-user"):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    return ws


def _room(gw, ws):
    room_id = "room-size-test"
    webid = gw._client_webids.get(ws, "did:key:size-user")
    gw._local_rooms[room_id] = {
        "name": "Size Test", "code": "x" * 64,
        "members": {ws}, "invite_url": "",
        "history_mode": "none", "messages": [],
        "creator_webid": webid,
    }
    return room_id


# ---------------------------------------------------------------------------
# Room send_room content guards
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_room_empty_content_rejected(gateway):
    ws = _registered_ws(gateway)
    room_id = _room(gateway, ws)
    await gateway._handle_send_room(ws, {"room_id": room_id, "content": ""})
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "empty_content" in resp.get("message", "")


@pytest.mark.asyncio
async def test_send_room_whitespace_only_rejected(gateway):
    ws = _registered_ws(gateway)
    room_id = _room(gateway, ws)
    await gateway._handle_send_room(ws, {"room_id": room_id, "content": "   \t\n  "})
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "empty_content" in resp.get("message", "")


@pytest.mark.asyncio
async def test_send_room_oversized_content_rejected(gateway):
    ws = _registered_ws(gateway)
    room_id = _room(gateway, ws)
    await gateway._handle_send_room(ws, {"room_id": room_id, "content": "x" * 20_000})
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "content_too_large" in resp.get("message", "")


@pytest.mark.asyncio
async def test_send_room_max_allowed_content(gateway):
    """16 KiB content (exactly at the boundary) should not be rejected for size."""
    ws = _registered_ws(gateway)
    room_id = _room(gateway, ws)
    big = "a" * 16_384
    await gateway._handle_send_room(ws, {"room_id": room_id, "content": big})
    # If there's a send call, it should NOT be an error about size
    for call in ws.send.call_args_list:
        msg = json.loads(call[0][0])
        assert "content_too_large" not in msg.get("message", "")


# ---------------------------------------------------------------------------
# local_dm content guards
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_local_dm_empty_content_rejected(gateway):
    ws = _registered_ws(gateway)
    await gateway._handle_local_dm(ws, {
        "target_webid": "did:key:peer",
        "content": "",
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "empty_content" in resp.get("message", "")


@pytest.mark.asyncio
async def test_local_dm_oversized_content_rejected(gateway):
    ws = _registered_ws(gateway)
    await gateway._handle_local_dm(ws, {
        "target_webid": "did:key:peer",
        "content": "z" * 17_000,
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "content_too_large" in resp.get("message", "")


# ---------------------------------------------------------------------------
# Schedule message 4 KiB cap
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_schedule_message_oversized_content_rejected(gateway):
    ws = _registered_ws(gateway)
    await gateway._handle_schedule_message(ws, {
        "thread_id": "t",
        "content": "s" * 5_000,
        "send_at": "2030-01-01T00:00:00+00:00",
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "content_too_large" in resp.get("message", "")


@pytest.mark.asyncio
async def test_schedule_message_4kb_limit_allowed(gateway):
    """4095-byte content should pass the size check."""
    ws = _registered_ws(gateway)
    await gateway._handle_schedule_message(ws, {
        "thread_id": "t",
        "content": "s" * 4_000,
        "send_at": "2030-01-01T00:00:00+00:00",
    })
    for call in ws.send.call_args_list:
        msg = json.loads(call[0][0])
        assert "content_too_large" not in msg.get("message", "")
