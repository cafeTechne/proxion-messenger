"""Tests for short invite token, QR share, and well-known version — R17.5 / R18.4."""
import asyncio
import json
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


def _start_gateway(tmp_path):
    agent = AgentState.generate()
    ws_port = _free_port()
    http_port = _free_port()
    cfg = GatewayConfig(
        host="127.0.0.1", port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}",
        db_path=str(tmp_path / "gw.db"),
    )
    gw = ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=cfg, read_state=ReadState())

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

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return gw, http_port, ws_port, ready


# ── R17.5.1: GET /i/<token> redirects to invite page ─────────────────────────


@pytest.mark.asyncio
async def test_short_invite_redirect_valid_token(tmp_path):
    """R17.5.1: GET /i/<token> with the correct token returns 302 to /invite?from=..."""
    gw, http_port, _, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.2)

    token = gw._short_invite_token
    assert token, "gateway must have a short invite token"

    resp = httpx.get(
        f"http://127.0.0.1:{http_port}/i/{token}",
        follow_redirects=False,
        timeout=5,
    )
    assert resp.status_code == 302
    location = resp.headers.get("location", "")
    assert "/invite" in location
    assert "from=" in location


# ── R17.5.2: GET /i/<bad-token> returns 404 ──────────────────────────────────


@pytest.mark.asyncio
async def test_short_invite_redirect_bad_token(tmp_path):
    """R17.5.2: GET /i/<wrong-token> returns 404."""
    gw, http_port, _, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.2)

    resp = httpx.get(
        f"http://127.0.0.1:{http_port}/i/00000000",
        follow_redirects=False,
        timeout=5,
    )
    assert resp.status_code == 404


# ── R17.5.3: my_address event includes short_invite_url ──────────────────────


@pytest.mark.asyncio
async def test_my_address_includes_short_invite_url(tmp_path):
    """R17.5.3: get_my_address WS response includes short_invite_url."""
    from proxion_messenger_core.didkey import pub_key_to_did
    gw, http_port, ws_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.2)

    my_did = pub_key_to_did(gw.agent.identity_pub_bytes)

    async with websockets.connect(f"ws://127.0.0.1:{ws_port}") as conn:
        await conn.send(json.dumps({"cmd": "register", "did": my_did}))
        await asyncio.sleep(0.1)
        await conn.send(json.dumps({"cmd": "get_my_address"}))

        deadline = asyncio.get_event_loop().time() + 3.0
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(conn.recv(), timeout=0.5)
                msg = json.loads(raw)
                if msg.get("type") == "my_address":
                    assert "short_invite_url" in msg
                    url = msg["short_invite_url"]
                    assert gw._short_invite_token in url
                    return
            except asyncio.TimeoutError:
                continue
    pytest.fail("my_address event not received")


# ── R17.5.4: invite URL from= param extraction logic ─────────────────────────


def test_from_param_extraction_from_invite_url():
    """R17.5.4: confirm that an invite URL with from= yields the proxion address."""
    from urllib.parse import urlparse, parse_qs, quote

    proxion_address = "did:key:zABC123@gateway.example.com"
    invite_url = f"http://127.0.0.1:8080/invite?from={quote(proxion_address)}"

    parsed = urlparse(invite_url)
    params = parse_qs(parsed.query)
    assert "from" in params
    assert params["from"][0] == proxion_address


# ── R18.4.1: /.well-known/proxion includes gateway_version ───────────────────


@pytest.mark.asyncio
async def test_well_known_includes_gateway_version(tmp_path):
    """R18.4.1: /.well-known/proxion response includes gateway_version field."""
    gw, http_port, _, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.2)

    resp = httpx.get(f"http://127.0.0.1:{http_port}/.well-known/proxion", timeout=5)
    assert resp.status_code == 200
    data = resp.json()
    assert "gateway_version" in data
    assert isinstance(data["gateway_version"], str)
    assert data["gateway_version"]


# ── R18.4.3: /i/<token> is stable across two requests in the same session ────


@pytest.mark.asyncio
async def test_short_invite_token_stable_in_session(tmp_path):
    """R18.4.3: The same short invite token redirects consistently (stable per gateway instance)."""
    gw, http_port, _, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.2)

    token = gw._short_invite_token

    r1 = httpx.get(f"http://127.0.0.1:{http_port}/i/{token}", follow_redirects=False, timeout=5)
    r2 = httpx.get(f"http://127.0.0.1:{http_port}/i/{token}", follow_redirects=False, timeout=5)

    assert r1.status_code == 302
    assert r2.status_code == 302
    assert r1.headers["location"] == r2.headers["location"]
