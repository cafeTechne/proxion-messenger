"""Tests: voice signals route via HTTP relay for cross-gateway peers."""
from __future__ import annotations
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore

@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    agent.identity_key.private_bytes = MagicMock(return_value=b"\x42" * 32)
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "test.db"))
    return gw

def _ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    return ws

@pytest.mark.asyncio
async def test_relay_voice_signal_calls_post_relay_when_gateway_known(gateway):
    """_relay_voice_signal posts to peer's relay URL when gateway URL is cached."""
    gateway._peer_gateway_urls["did:key:zBob"] = "https://bob.example.com"
    gateway._store.save_peer_gateway("did:key:zBob", "https://bob.example.com")

    with patch("proxion_messenger_core.relay.sign_relay_message", return_value="sig"), \
         patch("proxion_messenger_core.relay.post_relay", new_callable=AsyncMock, return_value=True) as mock_post:
        result = await gateway._relay_voice_signal("did:key:zBob", "ice_candidate", {"session_id": "s1", "candidate": "a=..."})

    assert result is True
    mock_post.assert_called_once()
    url = mock_post.call_args[0][0]
    assert url.endswith("/relay")

@pytest.mark.asyncio
async def test_relay_voice_signal_returns_false_when_no_gateway(gateway):
    """_relay_voice_signal returns False if peer gateway URL is not known."""
    result = await gateway._relay_voice_signal("did:key:zUnknown", "ice_candidate", {"session_id": "s2"})
    assert result is False

@pytest.mark.asyncio
async def test_voice_signal_relay_delivered_to_connected_socket(gateway):
    """Inbound relay with content_type=voice_signal pushes to target's WebSocket."""
    ws = _ws()
    gateway.clients.add(ws)
    target_webid = "did:key:zAlice"
    gateway._client_webids[ws] = target_webid
    gateway._webid_sockets[target_webid] = {ws}

    status, body = await gateway._handle_voice_signal_relay({
        "to_webid": target_webid,
        "from_webid": "did:key:zBob",
        "signal_type": "ice_candidate",
        "session_id": "sess-1",
        "signal_data": {"candidate": "a=candidate:..."},
    })

    assert status == "200 OK"
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "voice_signal"
    assert sent["signal_type"] == "ice_candidate"

@pytest.mark.asyncio
async def test_voice_signal_relay_offline_returns_202(gateway):
    """Voice signal for offline target returns 202 without queuing."""
    status, body = await gateway._handle_voice_signal_relay({
        "to_webid": "did:key:zOffline",
        "from_webid": "did:key:zBob",
        "signal_type": "ice_candidate",
        "session_id": "sess-2",
        "signal_data": {},
    })
    assert status == "202 Accepted"
    assert "offline" in body

@pytest.mark.asyncio
async def test_voice_signal_not_added_to_relay_queue(gateway):
    """Voice signals are never stored in the relay queue."""
    # Simulate a relay POST with content_type=voice_signal - it goes through
    # _handle_voice_signal_relay which drops when offline
    before = dict(gateway._relay_queue)
    await gateway._handle_voice_signal_relay({
        "to_webid": "did:key:zOffline",
        "from_webid": "did:key:zBob",
        "signal_type": "hangup",
        "session_id": "sess-3",
        "signal_data": {},
    })
    assert gateway._relay_queue == before  # no new queue entry
