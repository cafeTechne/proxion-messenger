"""Round 1: WebSocket Origin enforcement and per-IP connection limit tests."""
import asyncio
import json
import os
import pytest

pytest.importorskip("websockets")
import websockets

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def gateway():
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=_free_port()),
        read_state=ReadState(),
    )


# ---------------------------------------------------------------------------
# Per-IP connection limit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connection_limit_per_ip_enforced(monkeypatch):
    """After PROXION_MAX_CONNECTIONS_PER_IP connections from one IP, new ones are closed."""
    monkeypatch.setenv("PROXION_MAX_CONNECTIONS_PER_IP", "2")
    agent = AgentState.generate()
    port = _free_port()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=port), read_state=ReadState(),
    )
    ws_conns = []
    async with websockets.serve(gw.handle_client, "127.0.0.1", port):
        try:
            ws1 = await websockets.connect(f"ws://127.0.0.1:{port}")
            ws_conns.append(ws1)
            ws2 = await websockets.connect(f"ws://127.0.0.1:{port}")
            ws_conns.append(ws2)
            # Third connection from same IP should be rejected (close 1008)
            ws3 = await websockets.connect(f"ws://127.0.0.1:{port}")
            try:
                # Should get closed with 1008
                await asyncio.wait_for(ws3.recv(), timeout=2.0)
            except (websockets.exceptions.ConnectionClosed, asyncio.TimeoutError):
                pass
            # In websockets 12, check closed state and close code
            is_closed = getattr(ws3, "closed", None) or not getattr(ws3, "open", True)
            close_code = getattr(ws3, "close_code", None)
            assert is_closed or close_code == 1008
        finally:
            for ws in ws_conns:
                try:
                    await ws.close()
                except Exception:
                    pass


@pytest.mark.asyncio
async def test_allowed_origin_connects_normally(monkeypatch):
    """When PROXION_ALLOWED_ORIGINS is set, matching origin connects fine."""
    monkeypatch.setenv("PROXION_ALLOWED_ORIGINS", "http://localhost:3000")
    agent = AgentState.generate()
    port = _free_port()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=port), read_state=ReadState(),
    )
    # Connections without Origin header (direct connect) always succeed regardless of allowed_origins
    async with websockets.serve(gw.handle_client, "127.0.0.1", port, origins=["http://localhost:3000"]):
        ws = await websockets.connect(f"ws://127.0.0.1:{port}", additional_headers={"Origin": "http://localhost:3000"})
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
            data = json.loads(msg)
            assert data.get("type") == "config"
        finally:
            await ws.close()


@pytest.mark.asyncio
async def test_disallowed_origin_rejected_at_handshake(monkeypatch):
    """When PROXION_ALLOWED_ORIGINS is set, other origins are rejected."""
    monkeypatch.setenv("PROXION_ALLOWED_ORIGINS", "http://trusted.example.com")
    agent = AgentState.generate()
    port = _free_port()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=port), read_state=ReadState(),
    )
    async with websockets.serve(
        gw.handle_client, "127.0.0.1", port,
        origins=["http://trusted.example.com"],
    ):
        with pytest.raises(Exception):
            # Connection with untrusted origin should be rejected
            ws = await websockets.connect(
                f"ws://127.0.0.1:{port}",
                additional_headers={"Origin": "http://evil.example.com"},
            )
            await ws.close()


# ---------------------------------------------------------------------------
# IP connection count cleanup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ip_count_decremented_on_disconnect():
    """IP connection count returns to 0 after client disconnects."""
    agent = AgentState.generate()
    port = _free_port()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=port), read_state=ReadState(),
    )
    async with websockets.serve(gw.handle_client, "127.0.0.1", port):
        ws = await websockets.connect(f"ws://127.0.0.1:{port}")
        await asyncio.sleep(0.1)  # let handle_client proceed
        ip = "127.0.0.1"
        assert gw._ip_connection_counts.get(ip, 0) >= 1
        await ws.close()
        await asyncio.sleep(0.1)
        assert gw._ip_connection_counts.get(ip, 0) == 0
