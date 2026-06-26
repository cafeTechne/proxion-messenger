"""Tests for multi-device / linked sessions gateway support."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState


@pytest.fixture
def agent():
    a = AgentState.generate()
    a.webid = "https://alice.pod/profile/card#me"
    return a


@pytest.fixture
def gateway(agent):
    return ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=GatewayConfig())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


async def _register(gw, ws, webid="https://alice.pod/profile/card#me", name="Alice"):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did":webid, "display_name": name})


@pytest.mark.asyncio
async def test_register_second_session_does_not_evict(gateway):
    """Second registration with same DID does not remove first socket."""
    ws1 = _mock_ws()
    ws2 = _mock_ws()
    gateway.clients.add(ws1)
    gateway.clients.add(ws2)
    await gateway.process_command(ws1, {"cmd": "register", "did":"https://alice.pod/profile/card#me", "display_name": "Alice"})
    await gateway.process_command(ws2, {"cmd": "register", "did":"https://alice.pod/profile/card#me", "display_name": "Alice"})
    sockets = gateway._webid_sockets.get("https://alice.pod/profile/card#me", set())
    assert ws1 in sockets, "First session should still be registered"
    assert ws2 in sockets, "Second session should also be registered"


@pytest.mark.asyncio
async def test_two_clients_same_did_both_receive(gateway):
    """Message in a shared room reaches all sockets of a given DID."""
    ws_alice1 = _mock_ws()
    ws_alice2 = _mock_ws()
    ws_bob = _mock_ws()
    gateway.clients.update({ws_alice1, ws_alice2, ws_bob})
    alice_did = "https://alice.pod/profile/card#me"
    bob_did = "https://bob.pod/profile/card#me"
    await gateway.process_command(ws_alice1, {"cmd": "register", "did":alice_did, "display_name": "Alice"})
    await gateway.process_command(ws_alice2, {"cmd": "register", "did":alice_did, "display_name": "Alice"})
    await gateway.process_command(ws_bob, {"cmd": "register", "did":bob_did, "display_name": "Bob"})
    room_id = "room-multi"
    gateway._local_rooms[room_id] = {"members": {ws_alice1, ws_alice2, ws_bob}, "messages": [], "history_mode": "none"}
    ws_alice1.send.reset_mock()
    ws_alice2.send.reset_mock()
    import uuid
    await gateway.process_command(ws_bob, {
        "cmd": "send_room", "room_id": room_id,
        "content": "hello both", "message_id": str(uuid.uuid4()),
    })
    assert ws_alice1.send.called, "Alice session 1 should receive message"
    assert ws_alice2.send.called, "Alice session 2 should receive message"


@pytest.mark.asyncio
async def test_list_sessions_returns_both(gateway):
    """list_sessions shows all active sessions for the caller's DID."""
    ws1 = _mock_ws()
    ws2 = _mock_ws()
    gateway.clients.update({ws1, ws2})
    did = "https://alice.pod/profile/card#me"
    await gateway.process_command(ws1, {"cmd": "register", "did":did, "display_name": "Alice"})
    await gateway.process_command(ws2, {"cmd": "register", "did":did, "display_name": "Alice"})
    ws1.send.reset_mock()
    await gateway.process_command(ws1, {"cmd": "list_sessions"})
    resp = json.loads(ws1.send.call_args[0][0])
    assert resp["type"] == "session_list"
    assert len(resp["sessions"]) == 2


@pytest.mark.asyncio
async def test_revoke_session_closes_target(gateway):
    """revoke_session sends session_revoked to target and closes it."""
    ws1 = _mock_ws()
    ws2 = _mock_ws()
    gateway.clients.update({ws1, ws2})
    did = "https://alice.pod/profile/card#me"
    await gateway.process_command(ws1, {"cmd": "register", "did":did, "display_name": "Alice"})
    await gateway.process_command(ws2, {"cmd": "register", "did":did, "display_name": "Alice"})
    ws1.send.reset_mock()
    await gateway.process_command(ws1, {"cmd": "list_sessions"})
    resp = json.loads(ws1.send.call_args[0][0])
    ws2_session = next(s for s in resp["sessions"] if not s["is_current"])
    ws2.send.reset_mock()
    await gateway.process_command(ws1, {"cmd": "revoke_session", "session_id": ws2_session["session_id"]})
    assert ws2.send.called
    revoked_calls = [json.loads(c[0][0]) for c in ws2.send.call_args_list]
    assert any(e.get("type") == "session_revoked" for e in revoked_calls)


@pytest.mark.asyncio
async def test_disconnect_one_session_preserves_other(gateway):
    """Disconnecting one session keeps the other in _webid_sockets."""
    ws1 = _mock_ws()
    ws2 = _mock_ws()
    gateway.clients.update({ws1, ws2})
    did = "https://alice.pod/profile/card#me"
    await gateway.process_command(ws1, {"cmd": "register", "did":did, "display_name": "Alice"})
    await gateway.process_command(ws2, {"cmd": "register", "did":did, "display_name": "Alice"})
    # Simulate ws1 disconnect
    gateway.clients.discard(ws1)
    sockets = gateway._webid_sockets.get(did, set())
    sockets.discard(ws1)
    gateway._client_webids.pop(ws1, None)
    assert ws2 in gateway._webid_sockets.get(did, set())


@pytest.mark.asyncio
async def test_send_to_identity_helper_skips_errors(gateway):
    """_send_to_identity does not crash when a socket raises an exception."""
    ws_good = _mock_ws()
    ws_bad = _mock_ws()
    ws_bad.send.side_effect = Exception("broken pipe")
    gateway.clients.update({ws_good, ws_bad})
    did = "https://alice.pod/profile/card#me"
    gateway._webid_sockets[did] = {ws_good, ws_bad}
    # Should not raise
    await gateway._send_to_identity(did, json.dumps({"type": "test"}))
    assert ws_good.send.called
