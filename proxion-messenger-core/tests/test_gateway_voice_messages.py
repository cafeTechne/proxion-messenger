"""Tests for send_voice_message gateway command."""
from __future__ import annotations

import base64
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

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


_SMALL_AUDIO = base64.b64encode(b"\x00" * 100).decode()


@pytest.mark.asyncio
async def test_voice_message_delivered_to_room(gateway):
    """send_voice_message broadcasts to all room members."""
    ws_alice = _mock_ws()
    ws_bob = _mock_ws()
    await _register(gateway, ws_alice)
    await _register(gateway, ws_bob, "https://bob.pod/profile/card#me")
    room_id = "room-voice"
    gateway._local_rooms[room_id] = {"members": {ws_alice, ws_bob}, "messages": [], "history_mode": "none"}
    await gateway.process_command(ws_alice, {
        "cmd": "send_voice_message",
        "thread_id": room_id,
        "audio_b64": _SMALL_AUDIO,
        "duration_ms": 3000,
    })
    bob_calls = [json.loads(c[0][0]) for c in ws_bob.send.call_args_list]
    voice_events = [e for e in bob_calls if e.get("content_type") == "audio"]
    assert voice_events, "Bob should receive voice message event"
    assert voice_events[0]["audio_b64"] == _SMALL_AUDIO


@pytest.mark.asyncio
async def test_voice_message_content_type_is_audio(gateway):
    """Voice message event has content_type='audio'."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-ct"
    gateway._local_rooms[room_id] = {"members": {ws}, "messages": [], "history_mode": "none"}
    await gateway.process_command(ws, {
        "cmd": "send_voice_message",
        "thread_id": room_id,
        "audio_b64": _SMALL_AUDIO,
        "duration_ms": 1000,
    })
    events = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    audio_events = [e for e in events if e.get("type") == "message"]
    assert audio_events and audio_events[0]["content_type"] == "audio"


@pytest.mark.asyncio
async def test_voice_message_too_large_rejected(gateway):
    """Audio payload over 700KB is rejected."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-big"
    gateway._local_rooms[room_id] = {"members": {ws}, "messages": [], "history_mode": "none"}
    big_audio = "A" * 750_000
    await gateway.process_command(ws, {
        "cmd": "send_voice_message",
        "thread_id": room_id,
        "audio_b64": big_audio,
        "duration_ms": 1000,
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "invalid_voice_payload" in resp["message"] or "large" in resp["message"].lower()


@pytest.mark.asyncio
async def test_voice_message_too_long_rejected(gateway):
    """Duration over 60 seconds is rejected."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-long"
    gateway._local_rooms[room_id] = {"members": {ws}, "messages": [], "history_mode": "none"}
    await gateway.process_command(ws, {
        "cmd": "send_voice_message",
        "thread_id": room_id,
        "audio_b64": _SMALL_AUDIO,
        "duration_ms": 61_000,
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "invalid_voice_payload" in resp["message"] or "long" in resp["message"].lower()


@pytest.mark.asyncio
async def test_voice_message_missing_audio_b64_rejected(gateway):
    """Missing audio_b64 returns error."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-empty"
    gateway._local_rooms[room_id] = {"members": {ws}, "messages": [], "history_mode": "none"}
    await gateway.process_command(ws, {
        "cmd": "send_voice_message",
        "thread_id": room_id,
        "audio_b64": "",
        "duration_ms": 1000,
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"


@pytest.mark.asyncio
async def test_voice_message_persisted_to_store(gateway):
    """save_voice_message is called on the store when store is present."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-store"
    gateway._local_rooms[room_id] = {"members": {ws}, "messages": [], "history_mode": "none"}
    mock_store = MagicMock()
    gateway._store = mock_store
    await gateway.process_command(ws, {
        "cmd": "send_voice_message",
        "thread_id": room_id,
        "audio_b64": _SMALL_AUDIO,
        "duration_ms": 2000,
    })
    assert mock_store.save_voice_message.called


# ── DM voice notes (were never delivered: built, saved, dropped) ──────────────

@pytest.fixture
def store_gateway(agent, tmp_path):
    """The module fixture has no store; DM thread resolution needs one."""
    return ProxionGateway(agent=agent, dm_clients={}, room_memberships={},
                          config=GatewayConfig(db_path=str(tmp_path / "vm.db")))


async def _register_full(gw, ws, webid):
    """Register with _webid_sockets populated so _send_to_identity works."""
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    gw._display_names[ws] = webid[-8:]
    gw._webid_sockets.setdefault(webid, set()).add(ws)


def _audio_events(ws):
    return [json.loads(c[0][0]) for c in ws.send.call_args_list
            if json.loads(c[0][0]).get("content_type") == "audio"]


@pytest.mark.asyncio
async def test_dm_voice_note_reaches_peer_and_echoes_sender(store_gateway):
    gateway = store_gateway
    """Regression: the DM branch built + saved the event but delivered to NOBODY
    — the peer never heard it live and the sender saw nothing after recording."""
    ws_a, ws_b = _mock_ws(), _mock_ws()
    await _register_full(gateway, ws_a, "did:key:zAlice")
    await _register_full(gateway, ws_b, "did:key:zBob")
    gateway._store.save_dm_thread("thread-ab", "did:key:zBob", None, owner_webid="did:key:zAlice")

    await gateway.process_command(ws_a, {
        "cmd": "send_voice_message", "thread_id": "thread-ab",
        "audio_b64": _SMALL_AUDIO, "duration_ms": 3000,
    })
    assert _audio_events(ws_b), "the DM peer must receive the voice note live"
    assert _audio_events(ws_a), "the sender must get an echo (their UI renders from it)"


@pytest.mark.asyncio
async def test_dm_voice_note_did_keyed_thread_falls_back_to_peer(gateway):
    """Local DM threads are keyed by the peer's did directly."""
    ws_a, ws_b = _mock_ws(), _mock_ws()
    await _register_full(gateway, ws_a, "did:key:zAlice")
    await _register_full(gateway, ws_b, "did:key:zBob")
    await gateway.process_command(ws_a, {
        "cmd": "send_voice_message", "thread_id": "did:key:zBob",
        "audio_b64": _SMALL_AUDIO, "duration_ms": 3000,
    })
    assert _audio_events(ws_b), "did-keyed thread must resolve to the peer"


@pytest.mark.asyncio
async def test_dm_voice_note_to_remote_peer_errors_explicitly(store_gateway):
    gateway = store_gateway
    """Cross-gateway voice notes aren't supported (audio can exceed the relay
    cap) — the sender must be TOLD, not left believing it delivered."""
    ws_a = _mock_ws()
    await _register_full(gateway, ws_a, "did:key:zAlice")
    gateway._store.save_dm_thread("thread-ar", "did:key:zRemote", None, owner_webid="did:key:zAlice")
    gateway._peer_gateway_urls["did:key:zRemote"] = "http://remote-gw.test"
    await gateway.process_command(ws_a, {
        "cmd": "send_voice_message", "thread_id": "thread-ar",
        "audio_b64": _SMALL_AUDIO, "duration_ms": 3000,
    })
    errs = [json.loads(c[0][0]) for c in ws_a.send.call_args_list
            if json.loads(c[0][0]).get("code") == "voice_note_remote_unsupported"]
    assert errs, "sender must get an explicit unsupported-remote error"


@pytest.mark.asyncio
async def test_voice_message_honors_client_message_id(gateway):
    """The client uploads the pod audio copy keyed by ITS message_id; minting a
    fresh uuid server-side orphaned that upload from the stored message."""
    ws_a, ws_b = _mock_ws(), _mock_ws()
    await _register_full(gateway, ws_a, "did:key:zAlice")
    await _register_full(gateway, ws_b, "did:key:zBob")
    await gateway.process_command(ws_a, {
        "cmd": "send_voice_message", "thread_id": "did:key:zBob",
        "audio_b64": _SMALL_AUDIO, "duration_ms": 3000,
        "message_id": "client-chosen-id-123",
    })
    evs = _audio_events(ws_b)
    assert evs and evs[0]["message_id"] == "client-chosen-id-123"
