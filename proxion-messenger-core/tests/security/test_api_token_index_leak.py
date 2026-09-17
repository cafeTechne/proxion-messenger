"""F1: the legacy PROXION_API_TOKEN must never be embedded in a page that can be
served over the public tunnel or to a non-loopback peer.

The token is what the R118 recovery gate accepts to run /backup, /restore, /export,
/import, and /security-snapshot. index.html carried it as an x-api-token meta on
every GET /, so any tunnel or LAN visitor could scrape it and present it as a Bearer
token to exfiltrate or overwrite the owner's identity keys. The meta is now injected
per-request only for a genuine local client: never while the tunnel is live (which
collapses peer_ip to loopback), and never for an untrusted origin. Genuine loopback
with no tunnel still gets it so in-app recovery keeps working on the desktop.

F9: the legacy PROXION_API_TOKEN is compared with hmac.compare_digest at the recovery
gate, matching the admin-token path.
"""
import asyncio
import socket
import time
import types

import pytest

pytest.importorskip("websockets")

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_gateway(tmp_path):
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
    return gw, http_port


def _write_web(tmp_path):
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text(
        "<html><head><title>t</title></head><body>hi</body></html>")
    return web_dir


def _set_tunnel_active(gw, active: bool):
    gw._tunnel = types.SimpleNamespace(status=lambda: {"state": "running"}) if active else None


async def _serve(gw, web_dir, http_port):
    """Run _serve_http on the running loop and return the task once it accepts."""
    task = asyncio.create_task(gw._serve_http(str(web_dir), http_port))
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            r, w = await asyncio.open_connection("127.0.0.1", http_port)
            w.close()
            break
        except OSError:
            await asyncio.sleep(0.02)
    else:
        task.cancel()
        raise RuntimeError("http port never accepted")
    return task


async def _get_index(http_port: int, extra: bytes = b"") -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", http_port)
    writer.write(b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\n" + extra + b"\r\n")
    await writer.drain()
    buf = b""
    while True:
        chunk = await asyncio.wait_for(reader.read(65536), timeout=5.0)
        if not chunk:
            break
        buf += chunk
    writer.close()
    return buf


# ── F1: token meta withheld from tunneled / non-loopback GET / ──────────────────

@pytest.mark.asyncio
async def test_index_omits_token_over_tunnel(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXION_API_TOKEN", "legacy-secret")
    gw, http_port = _make_gateway(tmp_path)
    web_dir = _write_web(tmp_path)
    task = await _serve(gw, web_dir, http_port)
    try:
        _set_tunnel_active(gw, True)
        resp = await _get_index(http_port)
        assert b"200 OK" in resp, resp[:200]
        assert b"x-api-token" not in resp, "token meta leaked over the tunnel"
        assert b"legacy-secret" not in resp
        # The non-secret gateway-url meta still ships.
        assert b"x-gateway-url" in resp
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_index_omits_token_for_untrusted_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXION_API_TOKEN", "legacy-secret")
    gw, http_port = _make_gateway(tmp_path)
    web_dir = _write_web(tmp_path)
    task = await _serve(gw, web_dir, http_port)
    try:
        _set_tunnel_active(gw, False)
        resp = await _get_index(http_port, extra=b"Origin: http://evil.example\r\n")
        assert b"200 OK" in resp, resp[:200]
        assert b"x-api-token" not in resp, "token meta leaked to an untrusted origin"
        assert b"legacy-secret" not in resp
    finally:
        task.cancel()


# ── F1: token meta still served to a genuine local client ───────────────────────

@pytest.mark.asyncio
async def test_index_includes_token_loopback_no_tunnel(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXION_API_TOKEN", "legacy-secret")
    gw, http_port = _make_gateway(tmp_path)
    web_dir = _write_web(tmp_path)
    task = await _serve(gw, web_dir, http_port)
    try:
        _set_tunnel_active(gw, False)
        resp = await _get_index(http_port)
        assert b"200 OK" in resp, resp[:200]
        assert b"x-api-token" in resp, "local in-app recovery lost its token"
        assert b"legacy-secret" in resp
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_index_no_token_meta_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("PROXION_API_TOKEN", raising=False)
    gw, http_port = _make_gateway(tmp_path)
    web_dir = _write_web(tmp_path)
    task = await _serve(gw, web_dir, http_port)
    try:
        _set_tunnel_active(gw, False)
        resp = await _get_index(http_port)
        assert b"200 OK" in resp, resp[:200]
        assert b"x-api-token" not in resp
    finally:
        task.cancel()


# ── F9: legacy token compared constant-time at the recovery gate ────────────────

async def _get_backup(http_port: int, token: str) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", http_port)
    writer.write(
        b"GET /backup HTTP/1.0\r\nHost: 127.0.0.1\r\n"
        b"Authorization: Bearer " + token.encode() + b"\r\n"
        b"X-Proxion-Passphrase: testpass\r\n\r\n")
    await writer.drain()
    resp = await asyncio.wait_for(reader.read(65536), timeout=5.0)
    writer.close()
    return resp


@pytest.mark.asyncio
async def test_recovery_legacy_token_wrong_rejected_over_tunnel(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXION_API_TOKEN", "legacy-secret")
    gw, http_port = _make_gateway(tmp_path)
    web_dir = _write_web(tmp_path)
    task = await _serve(gw, web_dir, http_port)
    try:
        _set_tunnel_active(gw, True)
        resp = await _get_backup(http_port, "WRONG")
        assert b"403" in resp, resp[:200]
        assert b"recovery_forbidden_over_tunnel" in resp, resp[:200]
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_recovery_legacy_token_correct_allowed_over_tunnel(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXION_API_TOKEN", "legacy-secret")
    gw, http_port = _make_gateway(tmp_path)
    web_dir = _write_web(tmp_path)
    task = await _serve(gw, web_dir, http_port)
    try:
        _set_tunnel_active(gw, True)
        resp = await _get_backup(http_port, "legacy-secret")
        assert b"recovery_forbidden_over_tunnel" not in resp, resp[:200]
        assert b"200 OK" in resp, resp[:200]
    finally:
        task.cancel()
