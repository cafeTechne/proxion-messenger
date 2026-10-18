"""The per-pair ring throttle stops call spam without blocking legitimate re-offers:
group-call mesh rebuilds (rejoin, ICE recovery) and a redial after a decline or a
cancelled call."""
import json

import pytest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import proxion_messenger_core._gateway_voice as gv
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState


def _did():
    return pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, o: self is o
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _last(ws, t):
    for c in reversed(ws.send.call_args_list):
        m = json.loads(c[0][0])
        if m.get("type") == t:
            return m


def _too_frequent(ws):
    return any(json.loads(c[0][0]).get("message") == "call_too_frequent"
               for c in ws.send.call_args_list)


@pytest.fixture
async def room(tmp_path):
    gv._voice_invite_ts.clear()
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "r.db")))
    a, b = _mock_ws(), _mock_ws()
    ad, bd = _did(), _did()
    for ws, d in ((a, ad), (b, bd)):
        gw.clients.add(ws)
        await gw.process_command(ws, {"cmd": "register", "did": d, "display_name": "x"})
    await gw.process_command(a, {"cmd": "chat_room_create", "name": "R"})
    created = _last(a, "room_created")
    await gw.process_command(b, {"cmd": "join_room", "code": created["code"]})
    yield gw, a, ad, b, bd, created["room_id"]
    gv._voice_invite_ts.clear()


async def _ring(gw, caller, target_did, sid, **extra):
    caller.send.reset_mock()
    await gw.process_command(caller, {"cmd": "voice_invite", "target_webid": target_did,
                                      "session_id": sid, "sdp_offer": "v=0", **extra})
    return not _too_frequent(caller)


async def test_group_mesh_reoffer_is_not_throttled(room):
    gw, a, ad, b, bd, rid = room
    await gw.process_command(a, {"cmd": "join_voice_channel", "room_id": rid})
    await gw.process_command(b, {"cmd": "join_voice_channel", "room_id": rid})
    assert await _ring(gw, b, ad, "m1", channel_id=rid)
    await gw.process_command(a, {"cmd": "voice_answer", "target_webid": bd,
                                 "session_id": "m1", "sdp_answer": "v=0"})
    # ICE failed: the caller rebuilds the peer and re-offers (voice.js), and
    # again after a leave + rejoin. Neither is a ring.
    assert await _ring(gw, b, ad, "m2", channel_id=rid), "ICE recovery re-offer was throttled"
    await gw.process_command(b, {"cmd": "leave_voice_channel", "channel_id": rid})
    await gw.process_command(b, {"cmd": "join_voice_channel", "channel_id": rid})
    for i in range(4):
        assert await _ring(gw, b, ad, f"r{i}", channel_id=rid), "rejoin re-offer was throttled"


async def test_channel_id_does_not_exempt_a_non_member(room):
    gw, a, ad, b, bd, rid = room
    await gw.process_command(a, {"cmd": "join_voice_channel", "room_id": rid})
    # b is not in the call: naming the channel must not lift the throttle.
    assert await _ring(gw, b, ad, "s1", channel_id=rid)
    assert not await _ring(gw, b, ad, "s2", channel_id=rid)


async def test_redial_after_decline_goes_through(room):
    gw, a, ad, b, bd, rid = room
    assert await _ring(gw, a, bd, "c1")
    await gw.process_command(b, {"cmd": "voice_hangup", "session_id": "c1", "reason": "declined"})
    assert await _ring(gw, a, bd, "c2"), "redial after a decline was blocked"
    assert "c2" in gw._voice_sessions


async def test_redial_after_cancel_goes_through(room):
    gw, a, ad, b, bd, rid = room
    assert await _ring(gw, a, bd, "c1")
    await gw.process_command(a, {"cmd": "voice_hangup", "session_id": "c1"})
    assert await _ring(gw, a, bd, "c2"), "redial after cancelling was blocked"


async def test_still_ringing_and_burst_are_throttled(room):
    gw, a, ad, b, bd, rid = room
    assert await _ring(gw, a, bd, "c1")
    assert not await _ring(gw, a, bd, "dup"), "a second ring while the first rings must be refused"
    for sid in ("c1", "c2"):
        await gw.process_command(b, {"cmd": "voice_hangup", "session_id": sid, "reason": "declined"})
        if sid == "c1":
            assert await _ring(gw, a, bd, "c2")
    assert await _ring(gw, a, bd, "c3")
    await gw.process_command(b, {"cmd": "voice_hangup", "session_id": "c3", "reason": "declined"})
    assert not await _ring(gw, a, bd, "c4"), "a fourth ring inside the window must be refused"
