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


def _seed_rel_voice(gw, peer_did, owner=""):
    gw._store.save_relationship({"certificate_id": "c-" + peer_did[-4:], "subject": "ab"*32, "created_at": 0, "expires_at": 2**31-1}, peer_did=peer_did, owner_webid=owner)

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

    with patch("proxion_messenger_core.relay.sign_relay_envelope", return_value="sig"), \
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
    _seed_rel_voice(gateway, "did:key:zBob")

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
async def test_voice_signal_relay_to_gateway_identity_reaches_local_client(gateway):
    """Cross-gateway voice: a signal addressed to THIS gateway's own identity (the
    DID half of the Proxion address) must reach the local user's browser, which
    registered under its OWN client DID, not the gateway DID. Without the
    one-gateway-per-user fallback this returns 202/offline and the call silently
    never connects — the same identity-routing bug that broke cross-gateway DMs."""
    from proxion_messenger_core.didkey import pub_key_to_did
    ws = _ws()
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "did:key:zBrowserClient"          # browser's own DID
    gateway._webid_sockets["did:key:zBrowserClient"] = {ws}

    gateway_did = pub_key_to_did(gateway.agent.identity_pub_bytes)  # the address DID peers use
    _seed_rel_voice(gateway, "did:key:zAlice")
    status, body = await gateway._handle_voice_signal_relay({
        "to_webid": gateway_did,
        "from_webid": "did:key:zAlice",
        "signal_type": "offer",
        "session_id": "sess-x",
        "signal_data": {"sdp": "v=0..."},
    })

    assert status == "200 OK"
    ws.send.assert_called_once()
    assert json.loads(ws.send.call_args[0][0])["signal_type"] == "offer"

@pytest.mark.asyncio
async def test_voice_signal_relay_offline_returns_202(gateway):
    """Voice signal for offline target returns 202 without queuing."""
    _seed_rel_voice(gateway, "did:key:zBob")
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


@pytest.mark.asyncio
async def test_voice_signal_relay_rejects_stranger(gateway):
    """A voice signal from a webid the recipient has no relationship with is
    ignored — a peer gateway can't spam voice invites / spoof the caller."""
    ws = _ws()
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "did:key:zAlice"
    gateway._webid_sockets["did:key:zAlice"] = {ws}
    # No relationship for zStranger.
    status, _ = await gateway._handle_voice_signal_relay({
        "to_webid": "did:key:zAlice", "from_webid": "did:key:zStranger",
        "signal_type": "offer", "session_id": "s", "signal_data": {"sdp": "x"},
    })
    assert status.startswith("202")
    ws.send.assert_not_called()


@pytest.mark.asyncio
async def test_voice_channel_join_relay_rejects_non_member(gateway):
    """A remote join to a room's voice channel must come from a room member."""
    room_id = "voice-room"
    gateway._local_rooms[room_id] = {"members": set(), "creator_webid": "did:key:zOwner"}
    gateway._store.add_room_member(room_id, "did:key:zMember")
    status, _ = await gateway._handle_voice_channel_join_relay({
        "channel_id": room_id, "from_webid": "did:key:zOutsider",
        "origin_gateway_url": "https://evil.test",
    })
    assert status.startswith("403")
    assert "did:key:zOutsider" not in gateway._voice_channels.get(room_id, {}).get("members", {})


# ── 1:1 call endings across gateways ────────────────────────────────────────────

def _local_client(gateway, webid):
    ws = _ws()
    gateway.clients.add(ws)
    gateway._client_webids[ws] = webid
    gateway._webid_sockets[webid] = {ws}
    return ws


@pytest.mark.asyncio
async def test_callee_hangup_without_local_session_relays_to_caller_gateway(gateway):
    """Cross-gateway the callee's gateway holds no session (the offer arrived by
    relay). Its decline/hangup must still be relayed to the caller, carrying the
    reason, or the caller rings out or sits in a dead call."""
    bob = _local_client(gateway, "did:key:zBob")
    _seed_rel_voice(gateway, "did:key:zAliceGW")
    with patch.object(gateway, "_relay_voice_signal", new_callable=AsyncMock, return_value=True) as relay:
        await gateway.process_command(bob, {
            "cmd": "voice_hangup", "session_id": "s-x", "target_webid": "did:key:zAliceGW",
            "reason": "declined"})
        await asyncio.sleep(0)
    relay.assert_called_once_with("did:key:zAliceGW", "hangup", {"session_id": "s-x", "reason": "declined"})


@pytest.mark.asyncio
async def test_sessionless_hangup_to_a_stranger_is_not_relayed(gateway):
    bob = _local_client(gateway, "did:key:zBob")
    with patch.object(gateway, "_relay_voice_signal", new_callable=AsyncMock) as relay:
        await gateway.process_command(bob, {
            "cmd": "voice_hangup", "session_id": "s-x", "target_webid": "did:key:zStranger"})
        await asyncio.sleep(0)
    relay.assert_not_called()


@pytest.mark.asyncio
async def test_hangup_reason_reaches_same_gateway_caller(gateway):
    alice = _local_client(gateway, "did:key:zAlice")
    bob = _local_client(gateway, "did:key:zBob")
    gateway._voice_sessions["s-b"] = {"caller_ws": alice, "callee_ws": bob, "answered": False,
                                      "caller_webid": "did:key:zAlice", "target_webid": "did:key:zBob"}
    await gateway.process_command(bob, {"cmd": "voice_hangup", "session_id": "s-b", "reason": "busy"})
    sent = [json.loads(c[0][0]) for c in alice.send.call_args_list]
    assert {"type": "voice_hangup", "session_id": "s-b", "reason": "busy"} in sent
    # Only the fixed vocabulary passes through.
    gateway._voice_sessions["s-c"] = {"caller_ws": alice, "callee_ws": bob, "answered": False,
                                      "caller_webid": "did:key:zAlice", "target_webid": "did:key:zBob"}
    await gateway.process_command(bob, {"cmd": "voice_hangup", "session_id": "s-c", "reason": "<b>x"})
    sent = [json.loads(c[0][0]) for c in alice.send.call_args_list]
    assert {"type": "voice_hangup", "session_id": "s-c"} in sent


@pytest.mark.asyncio
async def test_redial_allowed_right_after_a_connected_call(gateway):
    """The 30s per-pair invite throttle is for unanswered spam; once a call has
    connected and ended, calling back immediately must work."""
    import proxion_messenger_core._gateway_voice as _gv
    _gv._voice_invite_ts.clear()
    alice = _local_client(gateway, "did:key:zAlice")
    bob = _local_client(gateway, "did:key:zBob")
    _seed_rel_voice(gateway, "did:key:zBob")
    await gateway.process_command(alice, {"cmd": "voice_invite", "session_id": "c1",
                                          "target_webid": "did:key:zBob", "sdp_offer": "v=0"})
    await gateway.process_command(bob, {"cmd": "voice_answer", "session_id": "c1",
                                        "target_webid": "did:key:zAlice", "sdp_answer": "v=0"})
    await gateway.process_command(bob, {"cmd": "voice_hangup", "session_id": "c1"})
    alice.send.reset_mock()
    await gateway.process_command(alice, {"cmd": "voice_invite", "session_id": "c2",
                                          "target_webid": "did:key:zBob", "sdp_offer": "v=0"})
    errs = [json.loads(c[0][0]) for c in alice.send.call_args_list]
    assert not any(m.get("message") == "call_too_frequent" for m in errs)
    assert "c2" in gateway._voice_sessions
    # An unanswered ring that was hung up still throttles the redial.
    await gateway.process_command(alice, {"cmd": "voice_hangup", "session_id": "c2"})
    alice.send.reset_mock()
    await gateway.process_command(alice, {"cmd": "voice_invite", "session_id": "c3",
                                          "target_webid": "did:key:zBob", "sdp_offer": "v=0"})
    errs = [json.loads(c[0][0]) for c in alice.send.call_args_list]
    assert any(m.get("message") == "call_too_frequent" for m in errs)
    _gv._voice_invite_ts.clear()


@pytest.mark.asyncio
async def test_relayed_answer_and_hangup_track_the_caller_session(gateway):
    """The caller's gateway learns of the far side's answer and hangup only via
    relay: mark the session answered (so it is not expired as an unanswered ring
    and the redial throttle lifts) and drop it on hangup."""
    alice = _local_client(gateway, "did:key:zAlice")
    _seed_rel_voice(gateway, "did:key:zBobGW")
    gateway._voice_sessions["s-r"] = {"caller_ws": alice, "callee_ws": None, "answered": False,
                                      "caller_webid": "did:key:zAlice", "target_webid": "did:key:zBobGW"}
    base = {"to_webid": "did:key:zAlice", "from_webid": "did:key:zBobGW", "session_id": "s-r"}
    await gateway._handle_voice_signal_relay({**base, "signal_type": "answer", "signal_data": {"sdp_answer": "v=0"}})
    assert gateway._voice_sessions["s-r"]["answered"] is True
    await gateway._handle_voice_signal_relay({**base, "signal_type": "hangup", "signal_data": {}})
    assert "s-r" not in gateway._voice_sessions
    # A third gateway can't end someone else's session by naming its id.
    gateway._voice_sessions["s-q"] = {"caller_ws": alice, "callee_ws": None, "answered": False,
                                      "caller_webid": "did:key:zAlice", "target_webid": "did:key:zBobGW"}
    _seed_rel_voice(gateway, "did:key:zEveGW")
    await gateway._handle_voice_signal_relay({**base, "from_webid": "did:key:zEveGW", "session_id": "s-q",
                                              "signal_type": "hangup", "signal_data": {}})
    assert "s-q" in gateway._voice_sessions
