"""Tests for screenshare_started / screenshare_stopped gateway signaling."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock

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
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    return ws


async def _register(gw, ws, webid="https://alice.pod/profile/card#me"):
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    gw._display_names[ws] = "Alice"


@pytest.mark.asyncio
async def test_screenshare_started_relayed_to_other_party(gateway):
    """screenshare_started is forwarded to the other voice session participant."""
    ws_caller = _mock_ws()
    ws_callee = _mock_ws()
    await _register(gateway, ws_caller)
    await _register(gateway, ws_callee, "https://bob.pod/profile/card#me")
    session_id = "sess-screen-1"
    gateway._voice_sessions[session_id] = {"caller_ws": ws_caller, "callee_ws": ws_callee}
    await gateway.process_command(ws_caller, {
        "cmd": "screenshare_started",
        "session_id": session_id,
    })
    callee_calls = [json.loads(c[0][0]) for c in ws_callee.send.call_args_list]
    assert any(e.get("type") == "screenshare_started" for e in callee_calls)


@pytest.mark.asyncio
async def test_screenshare_stopped_relayed_to_other_party(gateway):
    """screenshare_stopped is forwarded to the other voice session participant."""
    ws_caller = _mock_ws()
    ws_callee = _mock_ws()
    await _register(gateway, ws_caller)
    await _register(gateway, ws_callee, "https://bob.pod/profile/card#me")
    session_id = "sess-screen-2"
    gateway._voice_sessions[session_id] = {"caller_ws": ws_caller, "callee_ws": ws_callee}
    await gateway.process_command(ws_callee, {
        "cmd": "screenshare_stopped",
        "session_id": session_id,
    })
    caller_calls = [json.loads(c[0][0]) for c in ws_caller.send.call_args_list]
    assert any(e.get("type") == "screenshare_stopped" for e in caller_calls)


@pytest.mark.asyncio
async def test_screenshare_unknown_session_is_noop(gateway):
    """screenshare_started with unknown session_id does not crash."""
    ws = _mock_ws()
    await _register(gateway, ws)
    await gateway.process_command(ws, {
        "cmd": "screenshare_started",
        "session_id": "nonexistent-session",
    })
    assert not ws.send.called


@pytest.mark.asyncio
async def test_screenshare_includes_from_webid(gateway):
    """screenshare_started event includes from_webid of the sharer."""
    ws_caller = _mock_ws()
    ws_callee = _mock_ws()
    await _register(gateway, ws_caller)
    await _register(gateway, ws_callee, "https://bob.pod/profile/card#me")
    session_id = "sess-screen-3"
    gateway._voice_sessions[session_id] = {"caller_ws": ws_caller, "callee_ws": ws_callee}
    await gateway.process_command(ws_caller, {
        "cmd": "screenshare_started",
        "session_id": session_id,
    })
    callee_calls = [json.loads(c[0][0]) for c in ws_callee.send.call_args_list]
    ss_events = [e for e in callee_calls if e.get("type") == "screenshare_started"]
    assert ss_events and ss_events[0]["from_webid"] == "https://alice.pod/profile/card#me"
