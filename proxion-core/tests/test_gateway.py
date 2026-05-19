import json
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.inbox import InboxEntry
from proxion_messenger_core.messaging import Message
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.federation import RelationshipCertificate
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.solid_client import SolidClient

@pytest.fixture
def mock_agent():
    return MagicMock(spec=AgentState)

@pytest.fixture
def gateway(mock_agent):
    return ProxionGateway(
        agent=mock_agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9999),
        read_state=ReadState()
    )

def test_gateway_config_defaults():
    config = GatewayConfig()
    assert config.host == "0.0.0.0"  # binds all interfaces for public deployment
    assert config.port == 7474
    assert config.poll_interval == 3.0

def test_event_serialization_dm(gateway):
    mock_cert = MagicMock(spec=RelationshipCertificate)
    mock_msg = Message(
        message_id="msg-123",
        cert_id="cert-abc",
        from_pub_hex="alice-pub",
        content="Hello!",
        timestamp=1712851200, # 2024-04-11 16:00:00
        signature="sig-123"
    )
    # mock_msg.created_at ... removed
    
    entry = InboxEntry(source="dm", cert=mock_cert, message=mock_msg)
    entry.thread_id = "thread-abc"
    
    event = gateway._entry_to_event(entry, "dm")
    
    assert event["type"] == "message"
    assert event["source"] == "dm"
    assert event["thread_id"] == "thread-abc"
    assert event["from_webid"] == "alice-pub"
    assert event["content"] == "Hello!"
    assert event["timestamp"] == "2024-04-11T16:00:00+00:00"
    assert event["message_id"] == "msg-123"

def test_event_serialization_room(gateway):
    mock_cert = MagicMock(spec=RelationshipCertificate)
    mock_msg = Message(
        message_id="msg-456",
        cert_id="cert-room",
        from_pub_hex="bob-pub",
        content="In the room",
        timestamp=1712851260,
        signature="sig-456"
    )
    # mock_msg.created_at ... removed
    
    entry = InboxEntry(source="room", cert=mock_cert, message=mock_msg)
    entry.thread_id = "room-xyz"
    
    event = gateway._entry_to_event(entry, "room")
    assert event["source"] == "room"
    assert event["thread_id"] == "room-xyz"
    assert event["content"] == "In the room"

def test_gateway_decrypts_before_broadcast(gateway):
    mock_cert = MagicMock(spec=RelationshipCertificate)
    mock_msg = Message(
        message_id="msg-789",
        cert_id="cert-abc",
        from_pub_hex="alice-pub",
        content="enc1:ciphertext",
        timestamp=1712851200,
        signature="sig-789"
    )
    # mock_msg.created_at ... removed
    entry = InboxEntry(source="dm", cert=mock_cert, message=mock_msg)
    
    with patch("proxion_messenger_core._gateway_pod.is_encrypted", return_value=True), \
         patch("proxion_messenger_core._gateway_pod.derive_message_key", return_value=b"key"), \
         patch("proxion_messenger_core._gateway_pod.decrypt_message", return_value="Plaintext"):
        
        event = gateway._entry_to_event(entry, "dm")
        assert event["content"] == "Plaintext"

@pytest.mark.asyncio
async def test_gateway_poll_loop_deduplication(gateway):
    # Setup mock inbox with one message
    mock_msg = Message(
        message_id="mid",
        cert_id="cid",
        from_pub_hex="p",
        content="c",
        timestamp=100,
        signature="s"
    )
    # mock_msg.created_at ... removed
    entry = InboxEntry(source="dm", cert=MagicMock(), message=mock_msg)
    entry.thread_id = "tid"
    
    # Mock broadcast
    gateway.broadcast = MagicMock(return_value=asyncio.Future())
    gateway.broadcast.return_value.set_result(None)

    # Add a dummy client so the no-clients optimisation doesn't skip the poll.
    _dummy_ws = MagicMock()
    gateway.clients.add(_dummy_ws)

    # First poll
    with patch("proxion_messenger_core._gateway_pod.poll_inbox", return_value=[entry]):
        # Run poll_loop once by setting stop event immediately after
        async def stop_soon():
            await asyncio.sleep(0.1)
            gateway._stop_event.set()
        
        asyncio.create_task(stop_soon())
        await gateway.poll_loop()
        
        assert gateway.broadcast.call_count == 1
        assert gateway.read_state.is_seen("mid")
    
    # Reset and second poll (same ID)
    gateway.broadcast.reset_mock()
    gateway._stop_event.clear()
    with patch("proxion_messenger_core._gateway_pod.poll_inbox", return_value=[entry]):
        asyncio.create_task(stop_soon())
        await gateway.poll_loop()
        
        # Should NOT broadcast again
        assert gateway.broadcast.call_count == 0


# ---------------------------------------------------------------------------
# Voice relay tests (Batch A, Round 28)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_voice_relay_answer_reaches_caller(gateway):
    """voice_answer must be relayed directly to the caller's websocket."""
    caller_ws = MagicMock()
    caller_ws.send = AsyncMock()
    callee_ws = MagicMock()
    callee_ws.send = AsyncMock()

    # Pre-register both clients and the session
    gateway._client_webids[caller_ws] = "did:key:caller-relay-1"
    gateway._client_webids[callee_ws] = "did:key:callee-relay-1"
    gateway._voice_sessions["sess-relay-1"] = {"caller_ws": caller_ws, "callee_ws": callee_ws, "caller_webid": "did:key:caller-relay-1", "target_webid": "did:key:callee-relay-1"}
    gateway.clients = {caller_ws, callee_ws}

    await gateway.process_command(callee_ws, {
        "cmd": "voice_answer",
        "session_id": "sess-relay-1",
        "sdp_answer": "v=0\r\no=...",
    })

    caller_ws.send.assert_called_once()
    sent = json.loads(caller_ws.send.call_args[0][0])
    assert sent["type"] == "voice_answer"
    assert sent["session_id"] == "sess-relay-1"
    assert sent["sdp_answer"] == "v=0\r\no=..."
    # callee should NOT receive the answer
    callee_ws.send.assert_not_called()


@pytest.mark.asyncio
async def test_voice_relay_ice_routes_to_other_party(gateway):
    """ice_candidate sent by caller must be relayed to callee, not back to caller."""
    caller_ws = MagicMock()
    caller_ws.send = AsyncMock()
    callee_ws = MagicMock()
    callee_ws.send = AsyncMock()

    gateway._client_webids[caller_ws] = "did:key:caller-ice-1"
    gateway._client_webids[callee_ws] = "did:key:callee-ice-1"
    gateway._voice_sessions["sess-ice-1"] = {"caller_ws": caller_ws, "callee_ws": callee_ws, "caller_webid": "did:key:caller-ice-1", "target_webid": "did:key:callee-ice-1"}
    gateway.clients = {caller_ws, callee_ws}

    await gateway.process_command(caller_ws, {
        "cmd": "ice_candidate",
        "session_id": "sess-ice-1",
        "candidate": "candidate:1 1 UDP ...",
        "sdp_mid": "0",
        "sdp_mline_index": 0,
    })

    callee_ws.send.assert_called_once()
    sent = json.loads(callee_ws.send.call_args[0][0])
    assert sent["type"] == "ice_candidate"
    assert sent["candidate"] == "candidate:1 1 UDP ..."
    caller_ws.send.assert_not_called()


@pytest.mark.asyncio
async def test_voice_hangup_notifies_other_party(gateway):
    """voice_hangup removes the session and notifies the other party."""
    caller_ws = MagicMock()
    caller_ws.send = AsyncMock()
    callee_ws = MagicMock()
    callee_ws.send = AsyncMock()

    gateway._client_webids[caller_ws] = "did:key:caller-hang-1"
    gateway._client_webids[callee_ws] = "did:key:callee-hang-1"
    gateway._voice_sessions["sess-hang-1"] = {"caller_ws": caller_ws, "callee_ws": callee_ws, "caller_webid": "did:key:caller-hang-1", "target_webid": "did:key:callee-hang-1"}
    gateway.clients = {caller_ws, callee_ws}

    await gateway.process_command(caller_ws, {
        "cmd": "voice_hangup",
        "session_id": "sess-hang-1",
    })

    # Session must be removed
    assert "sess-hang-1" not in gateway._voice_sessions
    # Callee must be notified
    callee_ws.send.assert_called_once()
    sent = json.loads(callee_ws.send.call_args[0][0])
    assert sent["type"] == "voice_hangup"
    assert sent["session_id"] == "sess-hang-1"
