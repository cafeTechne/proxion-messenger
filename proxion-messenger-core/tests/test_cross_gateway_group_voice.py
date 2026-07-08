"""End-to-end cross-gateway GROUP VOICE (R55 follow-up).

Two gateways, a shared room, two users. Proves the voice-channel mesh actually
forms across gateways: the remote joiner learns the existing member (previously
dropped because the joiner never created a local channel), and a voice offer +
answer route both directions between co-members who are NOT direct friends
(previously blocked by peer-gateway resolution + the anti-spoof / contact checks).
"""
from __future__ import annotations

import json
import asyncio

import pytest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did


def _did(priv):
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock(); ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _events(ws, type_):
    return [json.loads(c[0][0]) for c in ws.send.call_args_list
            if json.loads(c[0][0]).get("type") == type_]


@pytest.fixture
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


def _gw(tmp_path, name, port):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", http_public_url=f"http://127.0.0.1:{port}",
                             db_path=str(tmp_path / f"{name}.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


@pytest.mark.asyncio
async def test_cross_gateway_group_voice_mesh_forms(tmp_path, noauth_env, monkeypatch):
    gw_a = _gw(tmp_path, "a", 9101)   # hosts the room
    gw_b = _gw(tmp_path, "b", 9102)   # federated member's gateway
    a_url = gw_a._gateway_http_url()
    b_url = gw_b._gateway_http_url()
    ga_did = pub_key_to_did(gw_a.agent.identity_pub_bytes)
    gb_did = pub_key_to_did(gw_b.agent.identity_pub_bytes)

    alice = _did(Ed25519PrivateKey.generate())
    bob = _did(Ed25519PrivateKey.generate())
    channel_id = "room-voice-1"

    # Route relays to the right gateway by URL.
    async def _route(url, payload):
        target = gw_a if a_url.rstrip("/") in url else gw_b
        status, _ = await target._handle_relay_post(json.dumps(payload).encode())
        return status.startswith("2")
    monkeypatch.setattr("proxion_messenger_core.relay.post_relay", _route)

    # GA hosts room `channel_id`; Alice is a local member, Bob a federated member.
    ws_a = _mock_ws(); await _register(gw_a, ws_a, alice)
    gw_a._local_rooms[channel_id] = {"name": "R", "members": {ws_a}}
    gw_a._store.add_federated_room_member(channel_id, bob, b_url)
    # GB knows GA as a peer gateway (so it can resolve the room's host).
    gw_b._peer_gateway_urls[ga_did] = a_url
    ws_b = _mock_ws(); await _register(gw_b, ws_b, bob)

    # 1) Alice joins the voice channel locally on GA.
    await gw_a.process_command(ws_a, {"cmd": "join_voice_channel", "channel_id": channel_id})
    assert alice in gw_a._voice_channels[channel_id]["members"]

    # 2) Bob joins on GB → federated: GB registers a local channel + relays join to GA.
    ws_b.send.reset_mock()
    await gw_b.process_command(ws_b, {"cmd": "join_voice_channel", "channel_id": channel_id})
    await asyncio.sleep(0.05)
    # GB created a local channel entry for Bob (the core bug fix).
    assert channel_id in gw_b._voice_channels
    assert bob in gw_b._voice_channels[channel_id]["members"]
    # GA registered Bob as a remote member.
    assert bob in gw_a._voice_channels[channel_id]["members"]
    # Alice was told Bob joined.
    assert any(e["peer_webid"] == bob for e in _events(ws_a, "voice_peer_joined"))
    # Bob learned Alice is present (peer_present reached the joiner — the fix).
    assert any(e["peer_webid"] == alice for e in _events(ws_b, "voice_peer_present"))
    # GB now knows Alice's gateway (channel-scoped), for signaling routing.
    assert gw_b._voice_channel_peer_gateway(alice) == a_url

    # 3) Bob sends a voice offer to Alice (co-members, NOT friends) → routes to GA.
    ws_a.send.reset_mock()
    await gw_b.process_command(ws_b, {
        "cmd": "voice_invite", "target_webid": alice,
        "session_id": "sess-1", "sdp_offer": "OFFER_SDP",
    })
    await asyncio.sleep(0.05)
    invites = _events(ws_a, "voice_signal") + _events(ws_a, "voice_invite")
    assert any("OFFER_SDP" in json.dumps(e) for e in invites), \
        "Alice must receive Bob's cross-gateway group-voice offer"

    # 4) Alice answers Bob → routes back to GB.
    ws_b.send.reset_mock()
    await gw_a.process_command(ws_a, {
        "cmd": "voice_answer", "target_webid": bob,
        "session_id": "sess-1", "sdp_answer": "ANSWER_SDP",
    })
    await asyncio.sleep(0.05)
    answers = _events(ws_b, "voice_signal") + _events(ws_b, "voice_answer")
    assert any("ANSWER_SDP" in json.dumps(e) for e in answers), \
        "Bob must receive Alice's cross-gateway group-voice answer"

    # 5) ICE candidates route too (Bob → Alice).
    ws_a.send.reset_mock()
    await gw_b.process_command(ws_b, {
        "cmd": "ice_candidate", "target_webid": alice,
        "session_id": "sess-1", "candidate": "CAND_XYZ",
    })
    await asyncio.sleep(0.05)
    ices = _events(ws_a, "voice_signal") + _events(ws_a, "ice_candidate")
    assert any("CAND_XYZ" in json.dumps(e) for e in ices), \
        "Alice must receive Bob's cross-gateway ICE candidate"
