"""Tests for Round 9 security hardening — CORS origin checks and auth auto-require."""
import asyncio
import json
import os
import socket
import threading

import pytest

pytest.importorskip("websockets")
import websockets
import httpx

from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.readstate import ReadState


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_gateway(tmp_path, host="127.0.0.1"):
    agent = AgentState.generate()
    ws_port = _free_port()
    http_port = _free_port()
    cfg = GatewayConfig(
        host=host, port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}",
        db_path=str(tmp_path / "gw.db"),
    )
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={}, config=cfg,
        read_state=ReadState(),
    )
    ready = threading.Event()
    loop = asyncio.new_event_loop()

    def _run():
        asyncio.set_event_loop(loop)

        async def _serve():
            async with websockets.serve(gw.handle_client, "127.0.0.1", ws_port):
                task = asyncio.create_task(gw._serve_http(None, http_port))
                ready.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    task.cancel()

        try:
            loop.run_until_complete(_serve())
        except Exception:
            ready.set()

    threading.Thread(target=_run, daemon=True).start()
    # ready.set() fires right after create_task(_serve_http) — BEFORE the HTTP
    # port actually binds. Callers that immediately POST then race a not-yet-
    # listening server (flaky under full-suite load). Poll the port so we only
    # return once it accepts connections.
    ready.wait(timeout=5)
    import time as _time
    _deadline = _time.time() + 5
    while _time.time() < _deadline:
        try:
            with socket.create_connection(("127.0.0.1", http_port), timeout=0.5):
                break
        except OSError:
            _time.sleep(0.05)
    return gw, http_port, ws_port, ready


# ── 9.4.3: _is_trusted_origin unit tests ─────────────────────────────────────

@pytest.mark.parametrize("origin,port,expected", [
    (b"",                              8080, True),   # absent — server-to-server / same-origin
    (b"null",                          8080, False),  # R7: sandboxed-iframe spoofable — NOT trusted
    (b"NULL",                          8080, False),  # R7: case-variant of null — NOT trusted
    (b"http://127.0.0.1:8080",         8080, True),   # web mode exact match
    (b"http://localhost:8080",         8080, True),   # localhost variant
    (b"tauri://localhost",             8080, True),   # Tauri v1
    (b"https://tauri.localhost",       8080, True),   # Tauri v1 alternate
    (b"http://127.0.0.1:9999",         8080, False),  # wrong port
    (b"https://evil.example.com",      8080, False),  # third-party
    (b"http://evil.example.com",       8080, False),  # third-party HTTP
    (b"http://127.0.0.1:8080",         9090, False),  # right host, wrong port config
])
def test_is_trusted_origin(origin, port, expected):
    from proxion_messenger_core.gateway import ProxionGateway
    result = ProxionGateway._is_trusted_origin(origin, port)
    assert result is expected


# ── 9.4.1: POST /setup/pod with evil Origin → 403 ────────────────────────────

@pytest.mark.asyncio
async def test_setup_pod_untrusted_origin_returns_403(tmp_path):
    """R9.4.1: POST /setup/pod from a third-party origin is rejected."""
    gw, http_port, _, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.2)

    resp = httpx.post(
        f"http://127.0.0.1:{http_port}/setup/pod",
        json={"css_url": "https://solidcommunity.net", "email": "x", "password": "y"},
        headers={"Origin": "https://evil.example.com"},
        timeout=5,
    )
    assert resp.status_code == 403
    assert "forbidden" in resp.json().get("error", "").lower()


# ── 9.4.2: POST /setup/pod with no Origin → passes origin check ──────────────

@pytest.mark.asyncio
async def test_setup_pod_no_origin_passes_cors_check(tmp_path):
    """R9.4.2: POST /setup/pod with no Origin header passes the origin check."""
    gw, http_port, _, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.2)

    # Should reach the actual handler (fail with bad credentials, not 403)
    resp = httpx.post(
        f"http://127.0.0.1:{http_port}/setup/pod",
        json={"css_url": "http://127.0.0.1:1", "email": "x", "password": "y"},
        timeout=10,
    )
    assert resp.status_code != 403


# ── 9.4.4: POST /import with evil Origin → 403 ───────────────────────────────

@pytest.mark.asyncio
async def test_import_untrusted_origin_returns_403(tmp_path):
    """R9.4.4: POST /import from a third-party origin is rejected."""
    gw, http_port, _, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.2)

    resp = httpx.post(
        f"http://127.0.0.1:{http_port}/import",
        json={"messages": [], "contacts": []},
        headers={"Origin": "https://attacker.example.com"},
        timeout=5,
    )
    assert resp.status_code == 403


# ── 9.4.5: Auth auto-requires for non-loopback host ──────────────────────────

@pytest.mark.asyncio
async def test_auth_auto_required_for_non_loopback(tmp_path, monkeypatch):
    """R9.4.5: When host is not loopback and PROXION_REQUIRE_AUTH is unset,
    registering a did:key triggers an auth challenge."""
    monkeypatch.delenv("PROXION_REQUIRE_AUTH", raising=False)

    agent = AgentState.generate()
    ws_port = _free_port()
    http_port = _free_port()
    # Use a specific routable address (not wildcard/loopback) to trigger auto-require
    cfg = GatewayConfig(
        host="10.0.0.1", port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}",
        db_path=str(tmp_path / "gw.db"),
    )
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={}, config=cfg,
        read_state=ReadState(),
    )
    ready = threading.Event()
    loop = asyncio.new_event_loop()

    def _run():
        asyncio.set_event_loop(loop)

        async def _serve():
            async with websockets.serve(gw.handle_client, "127.0.0.1", ws_port):
                task = asyncio.create_task(gw._serve_http(None, http_port))
                ready.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    task.cancel()

        try:
            loop.run_until_complete(_serve())
        except Exception:
            ready.set()

    threading.Thread(target=_run, daemon=True).start()
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.2)

    from proxion_messenger_core.didkey import pub_key_to_did
    some_did = pub_key_to_did(agent.identity_pub_bytes)

    async with websockets.connect(f"ws://127.0.0.1:{ws_port}") as conn:
        await conn.send(json.dumps({"cmd": "register", "did": some_did}))
        deadline = asyncio.get_event_loop().time() + 3.0
        got_challenge = False
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(conn.recv(), timeout=0.5)
                msg = json.loads(raw)
                if msg.get("type") == "auth_challenge":
                    got_challenge = True
                    break
                # must NOT get a successful registration without challenge
                assert msg.get("type") != "contacts", \
                    "registration succeeded without auth challenge on non-loopback host"
            except asyncio.TimeoutError:
                continue
        assert got_challenge, "expected auth_challenge but none received"
