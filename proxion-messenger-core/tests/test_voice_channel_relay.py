"""Tests: cross-gateway voice channel join/leave relay."""
from __future__ import annotations
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
async def test_local_join_includes_gateway_url_in_peer_joined(gateway):
    """voice_peer_joined sent to existing local member includes gateway_url field."""
    local_ws = _ws()
    new_ws = _ws()
    channel_id = "room-voice-1"
    local_webid = "did:key:zAlice"
    new_webid = "did:key:zBob"
    gateway._client_webids[local_ws] = local_webid
    gateway._client_webids[new_ws] = new_webid
    gateway._local_rooms[channel_id] = {"name": "T", "members": {local_ws, new_ws}}
    gateway.clients.add(local_ws)
    gateway.clients.add(new_ws)

    # Alice joins first
    await gateway._handle_join_voice_channel(local_ws, {"channel_id": channel_id})
    # Bob joins — Alice should receive voice_peer_joined with gateway_url
    await gateway._handle_join_voice_channel(new_ws, {"channel_id": channel_id})

    alice_calls = [json.loads(c[0][0]) for c in local_ws.send.call_args_list]
    peer_joined = [c for c in alice_calls if c.get("type") == "voice_peer_joined"]
    assert len(peer_joined) == 1
    assert "gateway_url" in peer_joined[0]


@pytest.mark.asyncio
async def test_local_join_notifies_remote_members_via_relay(gateway):
    """When a local member joins, remote (relay) members get relay_ephemeral notification."""
    local_ws = _ws()
    channel_id = "room-voice-2"
    local_webid = "did:key:zAlice"
    remote_webid = "did:key:zBob"
    gateway._client_webids[local_ws] = local_webid
    gateway._local_rooms[channel_id] = {"name": "T", "members": {local_ws}}
    gateway.clients.add(local_ws)

    # Pre-populate a remote member
    gateway._voice_channels[channel_id] = {
        "members": {remote_webid: {"ws": None, "gateway_url": "https://bob.example.com"}}
    }

    tasks = []
    with patch("asyncio.create_task", side_effect=lambda c: tasks.append(c) or MagicMock()):
        await gateway._handle_join_voice_channel(local_ws, {"channel_id": channel_id})

    # Should have relayed to Bob's gateway
    assert len(tasks) > 0


@pytest.mark.asyncio
async def test_relay_join_registers_remote_member(gateway):
    """_handle_voice_channel_join_relay adds remote member to channel."""
    channel_id = "room-voice-3"
    gateway._local_rooms[channel_id] = {"name": "T", "members": set()}

    status, _ = await gateway._handle_voice_channel_join_relay({
        "channel_id": channel_id,
        "from_webid": "did:key:zBob",
        "origin_gateway_url": "https://bob.example.com",
    })

    assert status.startswith("200")
    channel = gateway._voice_channels.get(channel_id)
    assert channel is not None
    member = channel["members"].get("did:key:zBob")
    assert member is not None
    assert member["gateway_url"] == "https://bob.example.com"
    assert member["ws"] is None


@pytest.mark.asyncio
async def test_relay_join_notifies_local_members(gateway):
    """_handle_voice_channel_join_relay delivers voice_peer_joined to local members."""
    ws = _ws()
    channel_id = "room-voice-4"
    local_webid = "did:key:zAlice"
    gateway._client_webids[ws] = local_webid
    gateway._local_rooms[channel_id] = {"name": "T", "members": {ws}}
    gateway.clients.add(ws)
    # Alice is already in the channel
    gateway._voice_channels[channel_id] = {
        "members": {local_webid: {"ws": ws, "gateway_url": None}}
    }

    await gateway._handle_voice_channel_join_relay({
        "channel_id": channel_id,
        "from_webid": "did:key:zBob",
        "origin_gateway_url": "https://bob.example.com",
    })

    ws.send.assert_called()
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    joined_events = [c for c in calls if c.get("type") == "voice_peer_joined"]
    assert len(joined_events) == 1
    assert joined_events[0]["peer_webid"] == "did:key:zBob"
    assert joined_events[0]["gateway_url"] == "https://bob.example.com"


@pytest.mark.asyncio
async def test_relay_leave_removes_remote_member_and_notifies_locals(gateway):
    """_handle_voice_channel_leave_relay removes member and notifies local sockets."""
    ws = _ws()
    channel_id = "room-voice-5"
    local_webid = "did:key:zAlice"
    remote_webid = "did:key:zBob"
    gateway._client_webids[ws] = local_webid
    gateway._voice_channels[channel_id] = {
        "members": {
            local_webid: {"ws": ws, "gateway_url": None},
            remote_webid: {"ws": None, "gateway_url": "https://bob.example.com"},
        }
    }

    status, _ = await gateway._handle_voice_channel_leave_relay({
        "channel_id": channel_id,
        "from_webid": remote_webid,
    })

    assert status.startswith("200")
    assert remote_webid not in gateway._voice_channels[channel_id]["members"]
    ws.send.assert_called()
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    left = [c for c in calls if c.get("type") == "voice_peer_left"]
    assert len(left) == 1
    assert left[0]["peer_webid"] == remote_webid


# ── F10: joiner-side host pin for cross-gateway voice channels ──────────────
# A joiner-created channel entry stores host_gateway_url but previously seeded no
# gateway_dids, so _voice_channel_gateway_ok TOFU-accepted the FIRST signer of a
# peer_present/peer_joined relay. An attacker who won that race injected ghost
# peers the victim opened WebRTC toward. The join now pins the channel to the
# known host gateway did, and the peer handlers reject a non-host signer.

_HOST_URL = "https://host.example.com"
_HOST_DID = "did:key:zHost"
_ATTACKER_DID = "did:key:zEve"


@pytest.mark.asyncio
async def test_joiner_join_pins_host_gateway_did(gateway):
    """Joining a REMOTE room's voice channel seeds gateway_dids with the host did
    resolved from the known host gateway URL (kills blind first-signer TOFU)."""
    ws = _ws()
    channel_id = "room-remote-1"
    joiner_webid = "did:key:zAlice"
    gateway._client_webids[ws] = joiner_webid
    gateway.clients.add(ws)
    # Trusted routing state: the host gateway did maps to the host URL (seeded by
    # prior federation). channel_id is NOT a local room here → joiner path.
    gateway._peer_gateway_urls[_HOST_DID] = _HOST_URL

    tasks = []
    with patch("asyncio.create_task", side_effect=lambda c: tasks.append(c) or MagicMock()):
        await gateway._handle_join_voice_channel(ws, {"channel_id": channel_id})

    channel = gateway._voice_channels.get(channel_id)
    assert channel is not None
    assert channel["host_gateway_url"] == _HOST_URL
    assert _HOST_DID in channel.get("gateway_dids", set())
    # Close the coroutines the patched create_task never awaited.
    for c in tasks:
        c.close()


@pytest.mark.asyncio
async def test_joiner_rejects_non_host_peer_present(gateway):
    """On a joiner-created channel, a signed peer_present from a NON-host gateway is
    rejected — no ghost peer is registered — while the pinned host is accepted."""
    ws = _ws()
    channel_id = "room-remote-2"
    joiner_webid = "did:key:zAlice"
    gateway._client_webids[ws] = joiner_webid
    gateway.clients.add(ws)
    gateway._peer_gateway_urls[_HOST_DID] = _HOST_URL
    gateway._voice_channels[channel_id] = {
        "members": {joiner_webid: {"ws": ws, "gateway_url": None}},
        "host_gateway_url": _HOST_URL,
        "gateway_dids": {_HOST_DID},
    }

    # Attacker (unknown gateway did) races a peer_present in first.
    await gateway._handle_voice_channel_peer_present_relay({
        "channel_id": channel_id,
        "peer_webid": "did:key:zGhost",
        "peer_gateway_url": "https://evil.example.com",
        "relay_sig_did": _ATTACKER_DID,
    })
    assert "did:key:zGhost" not in gateway._voice_channels[channel_id]["members"]

    # The pinned host's peer_present is accepted and registers the real peer.
    await gateway._handle_voice_channel_peer_present_relay({
        "channel_id": channel_id,
        "peer_webid": "did:key:zBob",
        "peer_gateway_url": _HOST_URL,
        "relay_sig_did": _HOST_DID,
    })
    assert "did:key:zBob" in gateway._voice_channels[channel_id]["members"]


@pytest.mark.asyncio
async def test_joiner_rejects_non_host_peer_joined(gateway):
    """peer_joined from a non-host signer is likewise rejected on a joiner channel."""
    ws = _ws()
    channel_id = "room-remote-3"
    joiner_webid = "did:key:zAlice"
    gateway._client_webids[ws] = joiner_webid
    gateway.clients.add(ws)
    gateway._webid_sockets[joiner_webid] = {ws}
    gateway._peer_gateway_urls[_HOST_DID] = _HOST_URL
    gateway._voice_channels[channel_id] = {
        "members": {joiner_webid: {"ws": ws, "gateway_url": None}},
        "host_gateway_url": _HOST_URL,
        "gateway_dids": {_HOST_DID},
    }

    await gateway._handle_voice_channel_peer_joined_relay({
        "channel_id": channel_id,
        "target_webid": joiner_webid,
        "peer_webid": "did:key:zGhost",
        "peer_gateway_url": "https://evil.example.com",
        "relay_sig_did": _ATTACKER_DID,
    })
    assert "did:key:zGhost" not in gateway._voice_channels[channel_id]["members"]

    await gateway._handle_voice_channel_peer_joined_relay({
        "channel_id": channel_id,
        "target_webid": joiner_webid,
        "peer_webid": "did:key:zBob",
        "peer_gateway_url": _HOST_URL,
        "relay_sig_did": _HOST_DID,
    })
    assert "did:key:zBob" in gateway._voice_channels[channel_id]["members"]


@pytest.mark.asyncio
async def test_local_channel_peer_present_not_blocked_by_joiner_pin(gateway):
    """The local-room path (channel_id in _local_rooms, no host pin) is unaffected:
    a member peer_present still delivers regardless of relay_sig_did."""
    ws = _ws()
    channel_id = "room-local-1"
    local_webid = "did:key:zAlice"
    member_webid = "did:key:zBob"
    gateway._client_webids[ws] = local_webid
    gateway.clients.add(ws)
    gateway._local_rooms[channel_id] = {"name": "R", "members": {ws}}
    gateway._store.add_room_member(channel_id, member_webid)
    gateway._voice_channels[channel_id] = {
        "members": {local_webid: {"ws": ws, "gateway_url": None}},
    }

    await gateway._handle_voice_channel_peer_present_relay({
        "channel_id": channel_id,
        "peer_webid": member_webid,
        "peer_gateway_url": _HOST_URL,
        "relay_sig_did": _HOST_DID,
    })
    assert member_webid in gateway._voice_channels[channel_id]["members"]
