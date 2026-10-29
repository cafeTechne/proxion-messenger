"""Tunnel control through the full dispatcher.

The local browser registers under its own session DID, not the gateway's account
DID, so the owner-only policy table alone refused the genuine local owner. The
dispatcher now accepts _is_tunnel_owner as the owner proof for start/stop_tunnel.
These tests pin both sides of that boundary.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import proxion_messenger_core.tunnel as tunnelmod
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState

SESSION_DID = "did:key:z6MkpTHR8VNsBxYAAWHut2Geadd9jSwuBV8xRVjqxHt8vDgp"


@pytest.fixture
def gw(monkeypatch):
    monkeypatch.delenv("PROXION_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("PROXION_PUBLIC_URL", raising=False)
    monkeypatch.setattr(tunnelmod, "find_cloudflared", lambda: None)
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients=[], room_memberships=[],
        config=GatewayConfig(host="127.0.0.1", port=9975), read_state=ReadState(),
    )


def _ws(gw, did, ip="127.0.0.1", headers=None):
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.request = MagicMock()
    ws.request.headers = headers or {"host": "127.0.0.1:8080"}
    gw.clients.add(ws)
    gw._client_webids[ws] = did
    gw._session_meta[ws] = {"ip_addr": ip}
    return ws


def _sent(ws):
    return [json.loads(c[0][0]) for c in ws.send.call_args_list]


def _forbidden(ws):
    return any(m.get("message") == "gateway_owner_only" for m in _sent(ws))


@pytest.mark.asyncio
async def test_local_owner_session_did_reaches_start_tunnel(gw):
    """Loopback browser with its own session DID: not refused, gets the
    cloudflared-absent status instead."""
    ws = _ws(gw, SESSION_DID)
    await gw.process_command(ws, {"cmd": "start_tunnel"})
    assert not _forbidden(ws)
    assert any(m.get("type") == "tunnel_status" and m.get("state") == "absent"
               for m in _sent(ws))


@pytest.mark.asyncio
async def test_local_owner_session_did_reaches_stop_tunnel(gw):
    ws = _ws(gw, SESSION_DID)
    await gw.process_command(ws, {"cmd": "stop_tunnel"})
    assert not _forbidden(ws)
    assert any(m.get("state") == "stopped" for m in _sent(ws))


@pytest.mark.asyncio
async def test_proxied_loopback_caller_is_refused(gw):
    """cloudflared or a reverse proxy connects from loopback but adds forwarding
    headers: that caller is remote and must not control the tunnel."""
    ws = _ws(gw, SESSION_DID, headers={"host": "x.trycloudflare.com",
                                       "cf-connecting-ip": "203.0.113.9"})
    await gw.process_command(ws, {"cmd": "start_tunnel"})
    assert _forbidden(ws)


@pytest.mark.asyncio
async def test_non_owner_refused_when_auth_enforced(gw):
    """With a live tunnel auth is forced on, and loopback is no longer trusted:
    a fresh self-claimed DID arriving over the tunnel is refused."""
    gw._force_auth = True
    gw._tunnel = MagicMock()
    ws = _ws(gw, SESSION_DID)
    await gw.process_command(ws, {"cmd": "stop_tunnel"})
    assert _forbidden(ws)
    assert gw._tunnel is not None


@pytest.mark.asyncio
async def test_remote_ip_refused(gw):
    ws = _ws(gw, SESSION_DID, ip="203.0.113.9")
    await gw.process_command(ws, {"cmd": "start_tunnel"})
    assert _forbidden(ws)


@pytest.mark.asyncio
async def test_account_did_allowed_when_auth_enforced(gw):
    gw._force_auth = True
    ws = _ws(gw, pub_key_to_did(gw.agent.identity_pub_bytes), ip="203.0.113.9")
    await gw.process_command(ws, {"cmd": "start_tunnel"})
    assert not _forbidden(ws)


@pytest.mark.asyncio
async def test_starting_connection_can_stop_its_tunnel_other_cannot(gw, monkeypatch):
    """The connection that opened the tunnel keeps owner standing after auth is
    forced on; a second loopback connection opened afterwards does not."""
    class _Mgr:
        def __init__(self):
            self._s = "stopped"
        def status(self):
            return {"state": self._s, "url": "https://t.trycloudflare.com"
                    if self._s == "running" else None, "error": None}
        async def start(self, port, timeout=30.0):
            self._s = "running"
            return self.status()
        async def stop(self):
            self._s = "stopped"

    monkeypatch.setattr(tunnelmod, "TunnelManager", _Mgr)
    monkeypatch.setattr(tunnelmod, "find_cloudflared", lambda: "/usr/bin/cloudflared")
    owner = _ws(gw, SESSION_DID)
    await gw.process_command(owner, {"cmd": "start_tunnel"})
    assert any(m.get("type") == "tunnel_ready" for m in _sent(owner))
    assert gw._auth_enforced()

    other = _ws(gw, "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK")
    await gw.process_command(other, {"cmd": "stop_tunnel"})
    assert _forbidden(other)
    assert gw._tunnel is not None

    await gw.process_command(owner, {"cmd": "stop_tunnel"})
    assert gw._tunnel is None
    assert not gw._auth_enforced()
    # After the stop the old connection holds no lingering tunnel pass.
    assert getattr(gw, "_tunnel_owner_ws", None) is None
