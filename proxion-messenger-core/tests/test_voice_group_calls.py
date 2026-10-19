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


def _room(gateway, channel_id, *sockets):
    """A voice channel belongs to a room; joiners must be in it."""
    room = gateway._local_rooms.setdefault(channel_id, {"name": channel_id, "members": set()})
    room["members"].update(sockets)

@pytest.mark.asyncio
async def test_join_voice_channel_signals_existing_members(gateway):
    """Second joiner causes voice_peer_joined to first member."""
    ws1 = _ws("did:key:zAlice")
    ws2 = _ws("did:key:zBob")
    gateway.clients.update({ws1, ws2})
    gateway._client_webids[ws1] = "did:key:zAlice"
    gateway._client_webids[ws2] = "did:key:zBob"

    _room(gateway, "ch-test", ws1, ws2)
    # Alice joins first
    await gateway._handle_join_voice_channel(ws1, {"channel_id": "ch-test"})
    # nobody else in channel yet (only the room's voice_channel_state)
    assert not [c for c in ws1.send.call_args_list
                if json.loads(c[0][0]).get("type") == "voice_peer_joined"]

    # Bob joins — Alice should be notified
    ws1.send.reset_mock()
    await gateway._handle_join_voice_channel(ws2, {"channel_id": "ch-test"})

    sent = [json.loads(c[0][0]) for c in ws1.send.call_args_list]
    sent = [e for e in sent if e["type"] == "voice_peer_joined"]
    assert sent and sent[0]["peer_webid"] == "did:key:zBob"

@pytest.mark.asyncio
async def test_leave_voice_channel_signals_remaining(gateway):
    """Leaving triggers voice_peer_left for remaining members."""
    ws1 = _ws("did:key:zAlice")
    ws2 = _ws("did:key:zBob")
    gateway.clients.update({ws1, ws2})
    gateway._client_webids[ws1] = "did:key:zAlice"
    gateway._client_webids[ws2] = "did:key:zBob"

    _room(gateway, "ch-leave", ws1, ws2)
    await gateway._handle_join_voice_channel(ws1, {"channel_id": "ch-leave"})
    await gateway._handle_join_voice_channel(ws2, {"channel_id": "ch-leave"})
    ws1.send.reset_mock()

    await gateway._handle_leave_voice_channel(ws2, {"channel_id": "ch-leave"})

    sent = [json.loads(c[0][0]) for c in ws1.send.call_args_list]
    sent = [e for e in sent if e["type"] == "voice_peer_left"]
    assert sent and sent[0]["peer_webid"] == "did:key:zBob"

@pytest.mark.asyncio
async def test_voice_channel_empty_after_all_leave(gateway):
    """Channel is deleted from _voice_channels when last member leaves."""
    ws1 = _ws("did:key:zAlice")
    gateway.clients.add(ws1)
    gateway._client_webids[ws1] = "did:key:zAlice"

    _room(gateway, "ch-empty", ws1)
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
        _room(gateway, "ch-crowded", ws)
        await gateway._handle_join_voice_channel(ws, {"channel_id": "ch-crowded"})

    # 7th joiner
    ws7 = _ws("did:key:z7")
    ws7.send = AsyncMock()
    gateway.clients.add(ws7)
    gateway._client_webids[ws7] = "did:key:z7"
    _room(gateway, "ch-crowded", ws7)
    await gateway._handle_join_voice_channel(ws7, {"channel_id": "ch-crowded"})

    # ws7 should have received a warning about crowding
    calls = [json.loads(c[0][0]) for c in ws7.send.call_args_list]
    warnings = [c for c in calls if c.get("type") == "warning" and c.get("code") == "voice_channel_crowded"]
    assert len(warnings) > 0


@pytest.mark.asyncio
async def test_join_unknown_channel_creates_no_state(gateway):
    """A channel id that is no room here and has no known host is refused, not
    opened as an ad-hoc channel."""
    ws = _ws("did:key:zAlice")
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "did:key:zAlice"
    await gateway._handle_join_voice_channel(ws, {"channel_id": "anything-at-all"})
    assert "anything-at-all" not in gateway._voice_channels
    sent = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    assert any(e.get("message") == "unknown_voice_channel" for e in sent)


@pytest.mark.asyncio
async def test_join_unknown_channel_not_relayed_to_an_arbitrary_peer(gateway):
    """With several peer gateways known and none recorded as the room's host,
    the join used to go to whichever peer came first."""
    from unittest.mock import patch
    ws = _ws("did:key:zAlice")
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "did:key:zAlice"
    gateway._peer_gateway_urls["did:key:zP1"] = "https://p1.example.com"
    gateway._peer_gateway_urls["did:key:zP2"] = "https://p2.example.com"
    with patch.object(gateway, "_relay_ephemeral", new_callable=AsyncMock) as relay:
        await gateway._handle_join_voice_channel(ws, {"channel_id": "room-x"})
        await asyncio.sleep(0)
    relay.assert_not_called()
    assert "room-x" not in gateway._voice_channels


@pytest.mark.asyncio
async def test_join_remote_room_goes_to_its_recorded_host(gateway):
    from unittest.mock import patch
    ws = _ws("did:key:zAlice")
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "did:key:zAlice"
    gateway._peer_gateway_urls["did:key:zP1"] = "https://p1.example.com"
    gateway._peer_gateway_urls["did:key:zP2"] = "https://p2.example.com"
    gateway._store.add_federated_room_member("room-y", "did:key:zHostUser", "https://p2.example.com")
    with patch.object(gateway, "_relay_ephemeral", new_callable=AsyncMock) as relay:
        await gateway._handle_join_voice_channel(ws, {"channel_id": "room-y"})
        await asyncio.sleep(0)
    assert relay.call_args[0][0] == "https://p2.example.com"
