import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.didkey import pub_key_to_did
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


def _make_agent():
    key = Ed25519PrivateKey.generate()
    agent = MagicMock(spec=AgentState)
    pub_bytes = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    agent.identity_pub_bytes = pub_bytes
    agent.identity_key = key
    return agent, pub_key_to_did(pub_bytes)


@pytest.fixture
def gateway(tmp_path):
    agent, _ = _make_agent()
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "voice.db")),
        read_state=ReadState(),
    )


@pytest.fixture
def voice_clients(gateway):
    import proxion_messenger_core._gateway_voice as _gv
    _gv._voice_invite_ts.clear()
    alice = MagicMock(); alice.send = AsyncMock()
    bob = MagicMock(); bob.send = AsyncMock()
    gateway.clients = {alice, bob}
    gateway._client_webids[alice] = "did:key:alice"
    gateway._client_webids[bob] = "did:key:bob"
    gateway._webid_sockets["did:key:alice"] = alice
    gateway._webid_sockets["did:key:bob"] = bob
    # Put both in a shared room so voice_invite passes the contact check
    gateway._local_rooms["voice-test-room"] = {
        "creator_webid": "did:key:alice",
        "members": {alice, bob},
    }
    return alice, bob


@pytest.mark.asyncio
async def test_voice_invite_routes_to_callee(gateway, voice_clients):
    alice, bob = voice_clients
    await gateway.process_command(alice, {
        "cmd": "voice_invite",
        "target_webid": "did:key:bob",
        "sdp_offer": "offer",
    })
    msgs = [json.loads(c[0][0]) for c in bob.send.call_args_list]
    assert any(m.get("type") == "voice_invite" for m in msgs)


@pytest.mark.asyncio
async def test_voice_answer_routes_to_caller(gateway, voice_clients):
    alice, bob = voice_clients
    gateway._voice_sessions["sess-1"] = {"caller_ws": alice, "callee_ws": bob, "answered": False, "caller_webid": "did:key:alice", "target_webid": "did:key:bob"}
    await gateway.process_command(bob, {"cmd": "voice_answer", "session_id": "sess-1", "sdp_answer": "ans"})
    msgs = [json.loads(c[0][0]) for c in alice.send.call_args_list]
    assert any(m.get("type") == "voice_answer" for m in msgs)


@pytest.mark.asyncio
async def test_voice_answer_double_rejected(gateway, voice_clients):
    alice, bob = voice_clients
    gateway._voice_sessions["sess-2"] = {"caller_ws": alice, "callee_ws": bob, "answered": True, "caller_webid": "did:key:alice", "target_webid": "did:key:bob"}
    await gateway.process_command(bob, {"cmd": "voice_answer", "session_id": "sess-2", "sdp_answer": "ans"})
    msgs = [json.loads(c[0][0]) for c in bob.send.call_args_list]
    assert any(m.get("type") == "error" for m in msgs)


@pytest.mark.asyncio
async def test_voice_hangup_clears_session(gateway, voice_clients):
    alice, bob = voice_clients
    gateway._voice_sessions["sess-3"] = {"caller_ws": alice, "callee_ws": bob, "answered": False, "caller_webid": "did:key:alice", "target_webid": "did:key:bob"}
    await gateway.process_command(alice, {"cmd": "voice_hangup", "session_id": "sess-3"})
    assert "sess-3" not in gateway._voice_sessions


@pytest.mark.asyncio
async def test_ice_candidate_routes_to_peer(gateway, voice_clients):
    alice, bob = voice_clients
    gateway._voice_sessions["sess-4"] = {"caller_ws": alice, "callee_ws": bob, "answered": False, "caller_webid": "did:key:alice", "target_webid": "did:key:bob"}
    await gateway.process_command(alice, {
        "cmd": "ice_candidate",
        "session_id": "sess-4",
        "candidate": "cand",
        "sdp_mid": "0",
        "sdp_mline_index": 0,
    })
    msgs = [json.loads(c[0][0]) for c in bob.send.call_args_list]
    assert any(m.get("type") == "ice_candidate" for m in msgs)


@pytest.mark.asyncio
async def test_voice_invite_ts_does_not_grow_without_bound(gateway, voice_clients):
    """Stale entries in _voice_invite_ts must be evicted so the dict stays bounded."""
    import time as _time
    from proxion_messenger_core._gateway_voice import _voice_invite_ts

    alice, bob = voice_clients

    # Clear any state left by other tests (module-level dict persists across tests)
    _voice_invite_ts.clear()

    # Pre-seed 100 expired entries (timestamp 60 s in the past — well beyond the 30 s window)
    stale_ts = _time.monotonic() - 60.0
    for i in range(100):
        _voice_invite_ts[(f"did:key:spam{i}", f"did:key:victim{i}")] = stale_ts

    pre_count = len(_voice_invite_ts)
    assert pre_count == 100

    # Trigger eviction via a legitimate invite from alice to bob
    await gateway.process_command(alice, {
        "cmd": "voice_invite",
        "target_webid": "did:key:bob",
        "sdp_offer": "v=0",
    })

    # Dict should now contain only the one fresh entry (alice→bob); all stale ones evicted
    post_count = len(_voice_invite_ts)
    assert post_count == 1, (
        f"Expected 1 entry after eviction (the fresh alice→bob invite), got {post_count}"
    )
