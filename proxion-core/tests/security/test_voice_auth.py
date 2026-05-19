"""Round 23 security tests: WebRTC voice session authorization — prevent
hijacking of voice sessions by unauthorized WebIDs."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _make_gateway(tmp_path):
    agent = AgentState.generate()
    cfg = GatewayConfig(db_path=str(tmp_path / "gw.db"))
    return ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=cfg)


def _fake_ws(gw, webid: str):
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    gw._client_webids[ws] = webid
    gw.clients.add(ws)
    return ws


def _priv_did():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return pub_key_to_did(pub)


def _inject_session(gw, session_id: str, caller_ws, callee_ws,
                    caller_webid: str, target_webid: str):
    """Directly plant a voice session in the gateway state."""
    gw._voice_sessions[session_id] = {
        "caller_ws": caller_ws,
        "callee_ws": callee_ws,
        "answered": False,
        "caller_webid": caller_webid,
        "target_webid": target_webid,
    }


# ── voice_answer ─────────────────────────────────────────────────────────────


class TestVoiceAnswerAuth:
    @pytest.mark.asyncio
    async def test_authorized_callee_can_answer(self, tmp_path):
        """The intended target can answer the call."""
        gw = _make_gateway(tmp_path)
        caller_did = _priv_did()
        callee_did = _priv_did()
        caller_ws = _fake_ws(gw, caller_did)
        callee_ws = _fake_ws(gw, callee_did)

        _inject_session(gw, "sess-1", caller_ws, callee_ws, caller_did, callee_did)
        await gw._handle_voice_answer(callee_ws, {"session_id": "sess-1", "sdp_answer": "sdp"})

        sent = [json.loads(c.args[0]) for c in caller_ws.send.call_args_list]
        assert any(m.get("type") == "voice_answer" for m in sent)
        assert not any(m.get("message") == "unauthorized" for m in sent)

    @pytest.mark.asyncio
    async def test_unauthorized_third_party_cannot_answer(self, tmp_path):
        """A third-party WebID not in the session receives 'unauthorized'."""
        gw = _make_gateway(tmp_path)
        caller_did = _priv_did()
        callee_did = _priv_did()
        attacker_did = _priv_did()
        caller_ws = _fake_ws(gw, caller_did)
        callee_ws = _fake_ws(gw, callee_did)
        attacker_ws = _fake_ws(gw, attacker_did)

        _inject_session(gw, "sess-2", caller_ws, callee_ws, caller_did, callee_did)
        await gw._handle_voice_answer(attacker_ws, {"session_id": "sess-2", "sdp_answer": "sdp"})

        responses = [json.loads(c.args[0]) for c in attacker_ws.send.call_args_list]
        assert any(m.get("message") == "unauthorized" for m in responses)
        # Caller must NOT receive a voice_answer event
        caller_msgs = [json.loads(c.args[0]) for c in caller_ws.send.call_args_list]
        assert not any(m.get("type") == "voice_answer" for m in caller_msgs)

    @pytest.mark.asyncio
    async def test_answer_does_not_alter_session_for_unauthorized(self, tmp_path):
        """Unauthorized answer must not modify the session state."""
        gw = _make_gateway(tmp_path)
        caller_did = _priv_did()
        callee_did = _priv_did()
        attacker_did = _priv_did()
        caller_ws = _fake_ws(gw, caller_did)
        callee_ws = _fake_ws(gw, callee_did)
        attacker_ws = _fake_ws(gw, attacker_did)

        _inject_session(gw, "sess-3", caller_ws, callee_ws, caller_did, callee_did)
        await gw._handle_voice_answer(attacker_ws, {"session_id": "sess-3", "sdp_answer": "evil"})

        sess = gw._voice_sessions.get("sess-3")
        assert sess is not None, "Session must still exist"
        assert not sess.get("answered"), "Session must not be marked answered by attacker"


# ── ice_candidate ─────────────────────────────────────────────────────────────


class TestIceCandidateAuth:
    @pytest.mark.asyncio
    async def test_authorized_caller_can_send_ice(self, tmp_path):
        """The caller can send ICE candidates."""
        gw = _make_gateway(tmp_path)
        caller_did = _priv_did()
        callee_did = _priv_did()
        caller_ws = _fake_ws(gw, caller_did)
        callee_ws = _fake_ws(gw, callee_did)

        _inject_session(gw, "sess-ice-1", caller_ws, callee_ws, caller_did, callee_did)
        await gw._handle_ice_candidate(caller_ws, {
            "session_id": "sess-ice-1", "candidate": "cand", "sdp_mid": "0", "sdp_mline_index": 0,
        })
        sent = [json.loads(c.args[0]) for c in callee_ws.send.call_args_list]
        assert any(m.get("type") == "ice_candidate" for m in sent)

    @pytest.mark.asyncio
    async def test_unauthorized_attacker_cannot_send_ice(self, tmp_path):
        """An attacker sending ICE candidates is rejected with 'unauthorized'."""
        gw = _make_gateway(tmp_path)
        caller_did = _priv_did()
        callee_did = _priv_did()
        attacker_did = _priv_did()
        caller_ws = _fake_ws(gw, caller_did)
        callee_ws = _fake_ws(gw, callee_did)
        attacker_ws = _fake_ws(gw, attacker_did)

        _inject_session(gw, "sess-ice-2", caller_ws, callee_ws, caller_did, callee_did)
        await gw._handle_ice_candidate(attacker_ws, {
            "session_id": "sess-ice-2", "candidate": "evil", "sdp_mid": "0", "sdp_mline_index": 0,
        })
        responses = [json.loads(c.args[0]) for c in attacker_ws.send.call_args_list]
        assert any(m.get("message") == "unauthorized" for m in responses)
        # callee must not receive anything
        assert not callee_ws.send.called


# ── voice_hangup ──────────────────────────────────────────────────────────────


class TestVoiceHangupAuth:
    @pytest.mark.asyncio
    async def test_authorized_caller_can_hangup(self, tmp_path):
        """The caller can hang up the call."""
        gw = _make_gateway(tmp_path)
        caller_did = _priv_did()
        callee_did = _priv_did()
        caller_ws = _fake_ws(gw, caller_did)
        callee_ws = _fake_ws(gw, callee_did)

        _inject_session(gw, "sess-hup-1", caller_ws, callee_ws, caller_did, callee_did)
        await gw._handle_voice_hangup(caller_ws, {"session_id": "sess-hup-1"})

        assert "sess-hup-1" not in gw._voice_sessions
        sent = [json.loads(c.args[0]) for c in callee_ws.send.call_args_list]
        assert any(m.get("type") == "voice_hangup" for m in sent)

    @pytest.mark.asyncio
    async def test_unauthorized_cannot_hangup(self, tmp_path):
        """An attacker cannot terminate a call they're not part of."""
        gw = _make_gateway(tmp_path)
        caller_did = _priv_did()
        callee_did = _priv_did()
        attacker_did = _priv_did()
        caller_ws = _fake_ws(gw, caller_did)
        callee_ws = _fake_ws(gw, callee_did)
        attacker_ws = _fake_ws(gw, attacker_did)

        _inject_session(gw, "sess-hup-2", caller_ws, callee_ws, caller_did, callee_did)
        await gw._handle_voice_hangup(attacker_ws, {"session_id": "sess-hup-2"})

        responses = [json.loads(c.args[0]) for c in attacker_ws.send.call_args_list]
        assert any(m.get("message") == "unauthorized" for m in responses)
        assert "sess-hup-2" in gw._voice_sessions, "Session must survive unauthorized hangup"
