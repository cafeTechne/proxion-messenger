"""The desktop webview reaches every gateway endpoint the web client fetches.

The webview is served from the Tauri asset origin, so each fetch to the sidecar
is cross-origin: it needs a CORS preflight answer (for POSTs with a JSON body or
custom headers) and an Access-Control-Allow-Origin on the response, or the
browser hides the body from the app. Other websites must still get neither.
"""
import asyncio
import re
from pathlib import Path

import pytest

pytest.importorskip("websockets")

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from gwharness import start_gateway, free_port

WEB = Path(__file__).resolve().parents[2] / "web"

# (method, path) for every gateway HTTP call the web client makes.
APP_CALLS = [
    ("GET", "/connectivity"), ("GET", "/health"), ("GET", "/.well-known/proxion"),
    ("GET", "/room-history/r1?code=x&limit=100"), ("POST", "/api/pod-disconnect"),
    ("POST", "/import"), ("GET", "/message-edits?message_id=m1"), ("GET", "/profile/x"),
    ("GET", "/vapid-public-key"), ("GET", "/backup"), ("POST", "/restore?dry_run=1"),
    ("POST", "/restore"), ("GET", "/turn-credentials"), ("POST", "/setup/pod"),
    ("GET", "/export"),
]


def test_app_calls_cover_every_gateway_fetch_in_web():
    called = set()
    for f in WEB.glob("*.js"):
        if f.name.endswith(".test.js") or "bundle" in f.name:
            continue
        for m in re.finditer(r"gatewayUrl\(\s*['\"`](/[^'\"`?$]*)", f.read_text(encoding="utf-8")):
            called.add(m.group(1))
    listed = [p for _, p in APP_CALLS]
    missing = sorted(c for c in called if not any(p.startswith(c) for p in listed))
    assert called, "no gatewayUrl() calls found in web/"
    assert not missing, f"web fetches not covered here: {missing}"


@pytest.fixture
def gw_port(tmp_path):
    ws, hp = free_port(), free_port()
    cfg = GatewayConfig(host="127.0.0.1", port=ws, http_port=hp,
                        public_url=f"ws://127.0.0.1:{ws}", db_path=str(tmp_path / "gw.db"))
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=cfg, read_state=ReadState())
    start_gateway(gw, ws, hp)
    return hp


def _run(coro):
    """Run *coro* on a private loop, leaving the thread's current loop alone
    (asyncio.run clears it, which breaks later tests that call get_event_loop)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _http(port: int, req: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(req)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(1 << 20), 10)
    writer.close()
    return data


def _head(resp: bytes):
    lines = resp.split(b"\r\n\r\n", 1)[0].split(b"\r\n")
    hdrs = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(b":")
        hdrs.setdefault(k.strip().lower().decode(), []).append(v.strip().decode())
    return lines[0].decode(), hdrs


def _request(method, path, origin, extra=b""):
    body = b"{}" if method == "POST" else b""
    return (f"{method} {path} HTTP/1.0\r\nOrigin: {origin}\r\n".encode() + extra
            + b"Content-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode() + b"\r\n\r\n" + body)


@pytest.mark.parametrize("origin", ["https://tauri.localhost", "tauri://localhost"])
def test_desktop_origin_can_read_every_app_endpoint(gw_port, origin):
    async def run():
        for method, path in APP_CALLS:
            status, hdrs = _head(await _http(gw_port, _request(method, path, origin)))
            acao = hdrs.get("access-control-allow-origin", [])
            assert len(acao) == 1 and acao[0] in (origin, "*"), (method, path, status, acao)

            pre = (f"OPTIONS {path} HTTP/1.0\r\nOrigin: {origin}\r\n"
                   f"Access-Control-Request-Method: {method}\r\n"
                   "Access-Control-Request-Headers: content-type, x-proxion-passphrase\r\n"
                   "Access-Control-Request-Private-Network: true\r\n\r\n").encode()
            status, hdrs = _head(await _http(gw_port, pre))
            assert status.split()[1] in ("200", "204"), (path, status)
            assert hdrs.get("access-control-allow-origin") in ([origin], ["*"]), (path, hdrs)
            allowed = ",".join(hdrs.get("access-control-allow-headers", [])).lower()
            assert "content-type" in allowed and "x-proxion-passphrase" in allowed, (path, hdrs)
            assert method in ",".join(hdrs.get("access-control-allow-methods", [])), (path, hdrs)
    _run(run())


def test_other_websites_still_get_no_acao(gw_port):
    async def run():
        for path in ("/export", "/message-edits?message_id=m1", "/backup", "/health"):
            _, hdrs = _head(await _http(gw_port, _request("GET", path, "https://evil.example")))
            assert hdrs.get("access-control-allow-origin") is None, (path, hdrs)
        pre = (b"OPTIONS /export HTTP/1.0\r\nOrigin: https://evil.example\r\n"
               b"Access-Control-Request-Method: GET\r\n\r\n")
        _, hdrs = _head(await _http(gw_port, pre))
        assert hdrs.get("access-control-allow-origin") is None
        # A proxied request carrying the desktop Origin is a remote caller.
        _, hdrs = _head(await _http(gw_port, _request(
            "GET", "/export", "https://tauri.localhost", b"X-Forwarded-For: 203.0.113.9\r\n")))
        assert hdrs.get("access-control-allow-origin") is None
    _run(run())
