"""Tests: voice channel group call mesh signaling."""
from __future__ import annotations
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
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

def _ws(webid):
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    return ws

@pytest.mark.asyncio
async def test_join_voice_channel_signals_existing_members(gateway):
    """Second joiner causes voice_peer_joined to first member."""
    ws1 = _ws("did:key:zAlice")
    ws2 = _ws("did:key:zBob")
    gateway.clients.update({ws1, ws2})
    gateway._client_webids[ws1] = "did:key:zAlice"
    gateway._client_webids[ws2] = "did:key:zBob"

    # Alice joins first
    await gateway._handle_join_voice_channel(ws1, {"channel_id": "ch-test"})
    ws1.send.assert_not_called()  # nobody else in channel yet

    # Bob joins — Alice should be notified
    ws1.send.reset_mock()
    await gateway._handle_join_voice_channel(ws2, {"channel_id": "ch-test"})

    ws1.send.assert_called()
    sent = json.loads(ws1.send.call_args_list[0][0][0])
    assert sent["type"] == "voice_peer_joined"
    assert sent["peer_webid"] == "did:key:zBob"

@pytest.mark.asyncio
async def test_leave_voice_channel_signals_remaining(gateway):
    """Leaving triggers voice_peer_left for remaining members."""
    ws1 = _ws("did:key:zAlice")
    ws2 = _ws("did:key:zBob")
    gateway.clients.update({ws1, ws2})
    gateway._client_webids[ws1] = "did:key:zAlice"
    gateway._client_webids[ws2] = "did:key:zBob"

    await gateway._handle_join_voice_channel(ws1, {"channel_id": "ch-leave"})
    await gateway._handle_join_voice_channel(ws2, {"channel_id": "ch-leave"})
    ws1.send.reset_mock()

    await gateway._handle_leave_voice_channel(ws2, {"channel_id": "ch-leave"})

    ws1.send.assert_called()
    sent = json.loads(ws1.send.call_args[0][0])
    assert sent["type"] == "voice_peer_left"
    assert sent["peer_webid"] == "did:key:zBob"

@pytest.mark.asyncio
async def test_voice_channel_empty_after_all_leave(gateway):
    """Channel is deleted from _voice_channels when last member leaves."""
    ws1 = _ws("did:key:zAlice")
    gateway.clients.add(ws1)
    gateway._client_webids[ws1] = "did:key:zAlice"

    await gateway._handle_join_voice_channel(ws1, {"channel_id": "ch-empty"})
    assert "ch-empty" in gateway._voice_channels

    await gateway._handle_leave_voice_channel(ws1, {"channel_id": "ch-empty"})
    assert "ch-empty" not in gateway._voice_channels

@pytest.mark.asyncio
async def test_voice_channel_crowded_sends_warning(gateway):
    """Joining a channel with 6 existing members sends a crowded warning."""
    webids = [f"did:key:z{i}" for i in range(6)]
    sockets = []
    for wid in webids:
        ws = _ws(wid)
        gateway.clients.add(ws)
        gateway._client_webids[ws] = wid
        sockets.append(ws)
        await gateway._handle_join_voice_channel(ws, {"channel_id": "ch-crowded"})

    # 7th joiner
    ws7 = _ws("did:key:z7")
    ws7.send = AsyncMock()
    gateway.clients.add(ws7)
    gateway._client_webids[ws7] = "did:key:z7"
    await gateway._handle_join_voice_channel(ws7, {"channel_id": "ch-crowded"})

    # ws7 should have received a warning about crowding
    calls = [json.loads(c[0][0]) for c in ws7.send.call_args_list]
    warnings = [c for c in calls if c.get("type") == "warning" and c.get("code") == "voice_channel_crowded"]
    assert len(warnings) > 0
