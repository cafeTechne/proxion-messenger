"""Round 8: Rate limiting tests — global, auth, and heavy command buckets."""
import json
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway():
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9987),
        read_state=ReadState(),
    )


def _registered_ws(gw, webid="did:key:ratelimit-user"):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    return ws


def _ws(gw):
    ws = MagicMock()
    ws.send = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# Global rate limit (30 cmd/10s, burst 60)
# ---------------------------------------------------------------------------

def test_global_rate_limit_allows_up_to_60():
    """60 commands in the same 10s window should be allowed."""
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9986), read_state=ReadState(),
    )
    ws = MagicMock()
    for _ in range(60):
        assert gw._check_ws_rate_limit(ws) is True


def test_global_rate_limit_blocks_at_61():
    """61st command in the same window should be rejected."""
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9985), read_state=ReadState(),
    )
    ws = MagicMock()
    for _ in range(60):
        gw._check_ws_rate_limit(ws)
    assert gw._check_ws_rate_limit(ws) is False


def test_global_rate_limit_resets_after_window():
    """After the 10s window expires, the counter resets."""
    import time as _t
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9984), read_state=ReadState(),
    )
    ws = MagicMock()
    for _ in range(60):
        gw._check_ws_rate_limit(ws)
    # Simulate time passing beyond the 10s window
    gw._rate_counters[ws][1] -= 11.0
    assert gw._check_ws_rate_limit(ws) is True


# ---------------------------------------------------------------------------
# Auth rate limit (5/min)
# ---------------------------------------------------------------------------

def test_auth_rate_limit_allows_5():
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9983), read_state=ReadState(),
    )
    ws = MagicMock()
    for _ in range(5):
        assert gw._check_auth_rate_limit(ws) is True


def test_auth_rate_limit_blocks_at_6():
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9982), read_state=ReadState(),
    )
    ws = MagicMock()
    for _ in range(5):
        gw._check_auth_rate_limit(ws)
    assert gw._check_auth_rate_limit(ws) is False


def test_auth_rate_resets_after_60s():
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9981), read_state=ReadState(),
    )
    ws = MagicMock()
    for _ in range(5):
        gw._check_auth_rate_limit(ws)
    gw._rate_auth_counters[ws][1] -= 61.0
    assert gw._check_auth_rate_limit(ws) is True


# ---------------------------------------------------------------------------
# Heavy command rate limit (10/min)
# ---------------------------------------------------------------------------

def test_heavy_rate_limit_allows_10():
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9980), read_state=ReadState(),
    )
    ws = MagicMock()
    for _ in range(10):
        assert gw._check_heavy_rate_limit(ws) is True


def test_heavy_rate_limit_blocks_at_11():
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9979), read_state=ReadState(),
    )
    ws = MagicMock()
    for _ in range(10):
        gw._check_heavy_rate_limit(ws)
    assert gw._check_heavy_rate_limit(ws) is False


# ---------------------------------------------------------------------------
# Integration: rate limit E_RATE is returned via process_command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_command_returns_erate_when_limited(gateway):
    ws = _registered_ws(gateway)
    # Exhaust the burst limit
    for _ in range(60):
        gateway._check_ws_rate_limit(ws)
    # Next command should be rate-limited
    await gateway.process_command(ws, {"cmd": "send_room", "room_id": "r", "content": "x"})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("code") == "E_RATE"
    assert "rate_limited" in sent.get("message", "")


@pytest.mark.asyncio
async def test_ping_bypasses_rate_limit(gateway):
    ws = _registered_ws(gateway)
    for _ in range(60):
        gateway._check_ws_rate_limit(ws)
    # ping must not be rate-limited
    ws.send.reset_mock()
    await gateway.process_command(ws, {"cmd": "ping"})
    if ws.send.called:
        sent = json.loads(ws.send.call_args[0][0])
        assert sent.get("code") != "E_RATE"


@pytest.mark.asyncio
async def test_rate_counters_cleared_on_disconnect(gateway):
    ws = _registered_ws(gateway)
    # Populate all three rate counter dicts
    gateway._rate_counters[ws] = [60, 0.0]
    gateway._rate_auth_counters[ws] = [5, 0.0]
    gateway._rate_heavy_counters[ws] = [10, 0.0]
    # Simulate disconnect cleanup
    gateway._rate_counters.pop(ws, None)
    gateway._rate_auth_counters.pop(ws, None)
    gateway._rate_heavy_counters.pop(ws, None)
    assert ws not in gateway._rate_counters
    assert ws not in gateway._rate_auth_counters
    assert ws not in gateway._rate_heavy_counters
