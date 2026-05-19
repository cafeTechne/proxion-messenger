"""Round 2: Voice message payload validation."""
import base64
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway():
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9965),
        read_state=ReadState(),
    )


def _ws(gw, webid="did:key:voice-user"):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    room_id = "room-voice-test"
    gw._local_rooms[room_id] = {
        "name": "VoiceRoom", "code": "x" * 64, "members": {ws},
        "invite_url": "", "history_mode": "none", "messages": [], "creator_webid": webid,
    }
    return ws, room_id


def _valid_audio_b64() -> str:
    return base64.b64encode(b"\x00" * 100).decode()


@pytest.mark.asyncio
async def test_reject_invalid_base64_audio(gateway):
    """audio_b64 that is not valid base64 → invalid_voice_payload."""
    ws, room_id = _ws(gateway)
    await gateway._handle_send_voice_message(ws, {
        "thread_id": room_id,
        "audio_b64": "!!!NOT_BASE64!!!",
        "duration_ms": 1000,
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "invalid_voice_payload" in resp["message"]


@pytest.mark.asyncio
async def test_reject_duration_below_minimum(gateway):
    """duration_ms < 250 → invalid_voice_payload."""
    ws, room_id = _ws(gateway)
    await gateway._handle_send_voice_message(ws, {
        "thread_id": room_id,
        "audio_b64": _valid_audio_b64(),
        "duration_ms": 100,
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "invalid_voice_payload" in resp["message"]


@pytest.mark.asyncio
async def test_reject_duration_above_maximum(gateway):
    """duration_ms > 60000 → invalid_voice_payload."""
    ws, room_id = _ws(gateway)
    await gateway._handle_send_voice_message(ws, {
        "thread_id": room_id,
        "audio_b64": _valid_audio_b64(),
        "duration_ms": 90000,
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "invalid_voice_payload" in resp["message"]


@pytest.mark.asyncio
async def test_accept_valid_voice_payload(gateway):
    """Valid audio_b64 with duration_ms=1000 is accepted."""
    ws, room_id = _ws(gateway)
    await gateway._handle_send_voice_message(ws, {
        "thread_id": room_id,
        "audio_b64": _valid_audio_b64(),
        "duration_ms": 1000,
    })
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    errors = [c for c in calls if c.get("type") == "error"]
    assert not errors, f"Valid voice payload should not produce error: {errors}"
    messages = [c for c in calls if c.get("type") == "message" and c.get("content_type") == "audio"]
    assert messages, f"Should deliver voice message event: {calls}"


@pytest.mark.asyncio
async def test_accept_boundary_duration_values(gateway):
    """duration_ms=250 and 60000 are both accepted."""
    for dur in (250, 60000):
        ws, room_id = _ws(gateway)
        await gateway._handle_send_voice_message(ws, {
            "thread_id": room_id,
            "audio_b64": _valid_audio_b64(),
            "duration_ms": dur,
        })
        calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
        errors = [c for c in calls if c.get("type") == "error"]
        assert not errors, f"duration_ms={dur} should be accepted: {errors}"
