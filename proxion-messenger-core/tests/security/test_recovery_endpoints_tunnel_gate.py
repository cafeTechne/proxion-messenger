"""F1: identity recovery/backup endpoints must not be reachable by an anonymous
remote caller over the app tunnel.

cloudflared proxies from 127.0.0.1, so while a tunnel is live a remote request
reaches the HTTP server with peer_ip=127.0.0.1 and (with no Origin header) passes
the loopback/trusted-origin fallback. GET /backup would then export the owner's
Ed25519 identity + X25519 store keys and POST /restore would replace the identity.
These endpoints now refuse a no-token caller while the tunnel is live, but keep the
genuine local single-user desktop path (loopback, no tunnel) working, and still
allow a caller presenting a provisioned token.
"""
import asyncio
import socket
import types

import pytest

pytest.importorskip("websockets")

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from gwharness import start_gateway as _serve_gw


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_gateway(tmp_path):
    agent = AgentState.generate()
    ws_port = _free_port()
    http_port = _free_port()
    cfg = GatewayConfig(
        host="127.0.0.1", port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}",
        db_path=str(tmp_path / "gw.db"),
    )
    gw = ProxionGateway(agent=agent, dm_clients={}, room_memberships={},
                        config=cfg, read_state=ReadState())
    handle = _serve_gw(gw, ws_port, http_port)
    return gw, handle.http_port, handle.ready


async def _http(http_port: int, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", http_port)
    writer.write(request)
    await writer.drain()
    resp = await asyncio.wait_for(reader.read(65536), timeout=5.0)
    writer.close()
    return resp


def _set_tunnel_active(gw, active: bool):
    # start_tunnel sets self._tunnel to a live TunnelManager; simulate that state.
    gw._tunnel = types.SimpleNamespace(status=lambda: {"state": "running"}) if active else None


# ── refused over a live tunnel (no token) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_backup_refused_over_tunnel_without_token(tmp_path):
    gw, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5)
    await asyncio.sleep(0.1)
    _set_tunnel_active(gw, True)
    resp = await _http(http_port,
        b"GET /backup HTTP/1.0\r\nHost: 127.0.0.1\r\nX-Proxion-Passphrase: p\r\n\r\n")
    assert b"403" in resp, resp[:200]
    assert b"recovery_forbidden_over_tunnel" in resp, resp[:200]


@pytest.mark.asyncio
async def test_restore_refused_over_tunnel_without_token(tmp_path):
    gw, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5)
    await asyncio.sleep(0.1)
    _set_tunnel_active(gw, True)
    resp = await _http(http_port,
        b"POST /restore HTTP/1.0\r\nHost: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\nContent-Length: 0\r\n\r\n")
    assert b"403" in resp, resp[:200]
    assert b"recovery_forbidden_over_tunnel" in resp, resp[:200]


@pytest.mark.asyncio
async def test_export_refused_over_tunnel_without_token(tmp_path):
    gw, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5)
    await asyncio.sleep(0.1)
    _set_tunnel_active(gw, True)
    resp = await _http(http_port, b"GET /export HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
    assert b"403" in resp, resp[:200]
    assert b"recovery_forbidden_over_tunnel" in resp, resp[:200]


@pytest.mark.asyncio
async def test_import_refused_over_tunnel_without_token(tmp_path):
    gw, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5)
    await asyncio.sleep(0.1)
    _set_tunnel_active(gw, True)
    resp = await _http(http_port,
        b"POST /import HTTP/1.0\r\nHost: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\nContent-Length: 0\r\n\r\n")
    assert b"403" in resp, resp[:200]
    assert b"recovery_forbidden_over_tunnel" in resp, resp[:200]


@pytest.mark.asyncio
async def test_security_snapshot_refused_over_tunnel_without_token(tmp_path):
    gw, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5)
    await asyncio.sleep(0.1)
    _set_tunnel_active(gw, True)
    resp = await _http(http_port,
        b"GET /security-snapshot HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
    assert b"403" in resp, resp[:200]
    assert b"recovery_forbidden_over_tunnel" in resp, resp[:200]


# ── allowed on genuine loopback with no tunnel ─────────────────────────────────

@pytest.mark.asyncio
async def test_backup_allowed_loopback_no_tunnel(tmp_path):
    gw, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5)
    await asyncio.sleep(0.1)
    _set_tunnel_active(gw, False)
    resp = await _http(http_port,
        b"GET /backup HTTP/1.0\r\nHost: 127.0.0.1\r\nX-Proxion-Passphrase: testpass\r\n\r\n")
    assert b"200 OK" in resp, resp[:200]
    assert b"recovery_forbidden_over_tunnel" not in resp


@pytest.mark.asyncio
async def test_export_allowed_loopback_no_tunnel(tmp_path):
    gw, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5)
    await asyncio.sleep(0.1)
    _set_tunnel_active(gw, False)
    resp = await _http(http_port, b"GET /export HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
    assert b"200 OK" in resp, resp[:200]


# ── allowed over a tunnel WITH a provisioned admin token ───────────────────────

@pytest.mark.asyncio
async def test_backup_allowed_over_tunnel_with_admin_token(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXION_ADMIN_API_TOKEN", "secret-admin-token")
    gw, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5)
    await asyncio.sleep(0.1)
    _set_tunnel_active(gw, True)
    resp = await _http(http_port,
        b"GET /backup HTTP/1.0\r\nHost: 127.0.0.1\r\n"
        b"Authorization: Bearer secret-admin-token\r\n"
        b"X-Proxion-Passphrase: testpass\r\n\r\n")
    assert b"recovery_forbidden_over_tunnel" not in resp, resp[:200]
    assert b"200 OK" in resp, resp[:200]


@pytest.mark.asyncio
async def test_backup_refused_over_tunnel_with_wrong_token(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXION_ADMIN_API_TOKEN", "secret-admin-token")
    gw, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5)
    await asyncio.sleep(0.1)
    _set_tunnel_active(gw, True)
    resp = await _http(http_port,
        b"GET /backup HTTP/1.0\r\nHost: 127.0.0.1\r\n"
        b"Authorization: Bearer WRONG\r\nX-Proxion-Passphrase: testpass\r\n\r\n")
    assert b"403" in resp, resp[:200]
    assert b"recovery_forbidden_over_tunnel" in resp, resp[:200]
