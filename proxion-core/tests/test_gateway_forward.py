"""Tests for forward_message gateway command."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def gateway(tmp_db):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x00" * 32
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9996, db_path=tmp_db),
        read_state=ReadState(),
    )


def _mock_ws(webid="https://alice.pod/profile/card#me"):
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    return ws


async def _register(gw, ws, webid="https://alice.pod/profile/card#me", name="Alice"):
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    gw._display_names[ws] = name
    gw._webid_sockets[webid] = ws


@pytest.mark.asyncio
async def test_forward_message_not_found_returns_error(gateway):
    ws = _mock_ws()
    await _register(gateway, ws)
    await gateway.process_command(ws, {
        "cmd": "forward_message",
        "message_id": "nonexistent-id",
        "target_thread_id": "room-xyz",
    })
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "error"
    assert "not found" in sent["message"].lower()


@pytest.mark.asyncio
async def test_forward_to_unjoined_thread_rejected(gateway):
    """Actor not in target thread → error."""
    ws = _mock_ws()
    ws2 = _mock_ws("https://bob.pod/profile/card#me")
    await _register(gateway, ws)
    await _register(gateway, ws2, "https://bob.pod/profile/card#me", "Bob")

    # Create a room that ws2 is in but ws is NOT in
    room_id = "room-private"
    gateway._local_rooms[room_id] = {"members": {ws2}, "messages": [], "history_mode": "none"}

    # Store a message from Bob
    import uuid
    msg_id = str(uuid.uuid4())
    gateway._local_rooms["room-public"] = {"members": {ws, ws2}, "messages": [], "history_mode": "none"}
    await gateway.process_command(ws, {
        "cmd": "send_room",
        "room_id": "room-public",
        "content": "hello",
        "message_id": msg_id,
    })
    ws.send.reset_mock()

    await gateway.process_command(ws, {
        "cmd": "forward_message",
        "message_id": msg_id,
        "target_thread_id": room_id,
    })
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "error"
    assert "not a member" in sent["message"].lower()


@pytest.mark.asyncio
async def test_forward_delivers_to_target_thread(gateway):
    """forward_message sends event to all members of target room."""
    ws_alice = _mock_ws()
    ws_bob = _mock_ws("https://bob.pod/profile/card#me")
    await _register(gateway, ws_alice)
    await _register(gateway, ws_bob, "https://bob.pod/profile/card#me", "Bob")

    src_room = "room-src"
    tgt_room = "room-tgt"
    gateway._local_rooms[src_room] = {"members": {ws_alice}, "messages": [], "history_mode": "none"}
    gateway._local_rooms[tgt_room] = {"members": {ws_alice, ws_bob}, "messages": [], "history_mode": "none"}

    import uuid
    msg_id = str(uuid.uuid4())
    await gateway.process_command(ws_alice, {
        "cmd": "send_room", "room_id": src_room,
        "content": "original message", "message_id": msg_id,
    })
    ws_alice.send.reset_mock()
    ws_bob.send.reset_mock()

    await gateway.process_command(ws_alice, {
        "cmd": "forward_message",
        "message_id": msg_id,
        "target_thread_id": tgt_room,
    })

    bob_calls = [json.loads(c[0][0]) for c in ws_bob.send.call_args_list]
    fwd_events = [e for e in bob_calls if e.get("type") == "message"]
    assert fwd_events, "Bob should receive the forwarded message"
    assert fwd_events[0].get("forwarded") is True


@pytest.mark.asyncio
async def test_forward_adds_forwarded_flag(gateway):
    """Forwarded event has forwarded=True."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-fwd"
    gateway._local_rooms[room_id] = {"members": {ws}, "messages": [], "history_mode": "none"}
    import uuid
    msg_id = str(uuid.uuid4())
    await gateway.process_command(ws, {
        "cmd": "send_room", "room_id": room_id,
        "content": "fwd me", "message_id": msg_id,
    })
    ws.send.reset_mock()
    await gateway.process_command(ws, {
        "cmd": "forward_message",
        "message_id": msg_id,
        "target_thread_id": room_id,
    })
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    msgs = [e for e in calls if e.get("type") == "message"]
    assert any(e.get("forwarded") is True for e in msgs)


@pytest.mark.asyncio
async def test_forward_preserves_content(gateway):
    """Forwarded message has same content as original."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-cnt"
    gateway._local_rooms[room_id] = {"members": {ws}, "messages": [], "history_mode": "none"}
    import uuid
    msg_id = str(uuid.uuid4())
    original_content = "this is the original content"
    await gateway.process_command(ws, {
        "cmd": "send_room", "room_id": room_id,
        "content": original_content, "message_id": msg_id,
    })
    ws.send.reset_mock()
    await gateway.process_command(ws, {
        "cmd": "forward_message",
        "message_id": msg_id,
        "target_thread_id": room_id,
    })
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    msgs = [e for e in calls if e.get("type") == "message" and e.get("forwarded")]
    assert msgs and msgs[0]["content"] == original_content


@pytest.mark.asyncio
async def test_forward_missing_fields_is_noop(gateway):
    """forward_message with missing fields does not crash, returns no error if missing actor."""
    ws = _mock_ws()
    await _register(gateway, ws)
    # No crash
    await gateway.process_command(ws, {"cmd": "forward_message"})
