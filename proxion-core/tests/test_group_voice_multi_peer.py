"""Tests: group voice multi-peer ICE/answer routing by webid."""
from __future__ import annotations
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


def _ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    return ws


@pytest.mark.asyncio
async def test_ice_candidate_routes_by_target_webid(gateway):
    """ICE candidate with target_webid goes to that peer's socket, not via session."""
    sender_ws = _ws()
    target_ws = _ws()
    sender_webid = "did:key:zAlice"
    target_webid = "did:key:zBob"
    gateway._client_webids[sender_ws] = sender_webid
    gateway._client_webids[target_ws] = target_webid
    gateway._webid_sockets[target_webid] = {target_ws}
    gateway.clients.add(sender_ws)
    gateway.clients.add(target_ws)

    await gateway._handle_ice_candidate(sender_ws, {
        "target_webid": target_webid,
        "session_id": "sess-grp-1",
        "candidate": "candidate:1 1 UDP ...",
        "sdp_mid": "0",
        "sdp_mline_index": 0,
    })

    target_ws.send.assert_called_once()
    sent = json.loads(target_ws.send.call_args[0][0])
    assert sent["type"] == "ice_candidate"
    assert sent["from_webid"] == sender_webid
    assert sent["candidate"].startswith("candidate:")


@pytest.mark.asyncio
async def test_ice_candidate_falls_back_to_session_without_target(gateway):
    """ICE candidate without target_webid uses the 1:1 session model (no crash)."""
    sender_ws = _ws()
    gateway._client_webids[sender_ws] = "did:key:zAlice"
    gateway.clients.add(sender_ws)
    # No session exists — should return quietly without raising
    await gateway._handle_ice_candidate(sender_ws, {
        "session_id": "nonexistent",
        "candidate": "candidate:...",
    })
    # No send to a target; sender may get nothing. The key assertion is no exception.


@pytest.mark.asyncio
async def test_voice_answer_routes_by_target_webid(gateway):
    """voice_answer with target_webid routes to that peer with from_webid set."""
    sender_ws = _ws()
    target_ws = _ws()
    sender_webid = "did:key:zBob"
    target_webid = "did:key:zAlice"
    gateway._client_webids[sender_ws] = sender_webid
    gateway._client_webids[target_ws] = target_webid
    gateway._webid_sockets[target_webid] = {target_ws}
    gateway.clients.add(sender_ws)
    gateway.clients.add(target_ws)

    await gateway._handle_voice_answer(sender_ws, {
        "target_webid": target_webid,
        "session_id": "sess-grp-2",
        "sdp_answer": "v=0...",
    })

    target_ws.send.assert_called_once()
    sent = json.loads(target_ws.send.call_args[0][0])
    assert sent["type"] == "voice_answer"
    assert sent["from_webid"] == sender_webid
    assert sent["sdp_answer"] == "v=0..."


@pytest.mark.asyncio
async def test_channel_join_then_leave_empties_channel(gateway):
    """Relay join then relay leave removes the channel entirely."""
    channel_id = "room-grp-1"
    gateway._local_rooms[channel_id] = {"name": "T", "members": set()}

    await gateway._handle_voice_channel_join_relay({
        "channel_id": channel_id,
        "from_webid": "did:key:zBob",
        "origin_gateway_url": "https://bob.example.com",
    })
    assert channel_id in gateway._voice_channels

    await gateway._handle_voice_channel_leave_relay({
        "channel_id": channel_id,
        "from_webid": "did:key:zBob",
    })
    assert channel_id not in gateway._voice_channels
