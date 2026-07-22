"""Tests for POST /setup/pod and GET /setup/pod endpoints — R16.5."""
import asyncio
import json
import socket
import pytest

pytest.importorskip("websockets")
import websockets
import httpx

from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
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
    gw = ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=cfg, read_state=ReadState())

    # Raises if the gateway fails to start or never accepts a connection, and
    # registers it for shutdown after the test (see tests/gwharness.py).
    handle = _serve_gw(gw, ws_port, http_port)
    return gw, handle.http_port, handle.ready


# ── R16.5.3: GET /setup/pod with no pod configured ────────────────────────


@pytest.mark.asyncio
async def test_get_setup_pod_no_pod_configured(tmp_path):
    """R16.5.3: GET /setup/pod returns connected: false when no pod is configured."""
    gw, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.2)

    resp = httpx.get(f"http://127.0.0.1:{http_port}/setup/pod", timeout=5)
    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is False
    assert data["pod_url"] is None


# ── R16.5.1 + R16.5.2: POST /setup/pod ────────────────────────────────────


@pytest.mark.asyncio
async def test_post_setup_pod_missing_fields_returns_400(tmp_path):
    """POST /setup/pod with missing fields returns 400."""
    gw, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.2)

    resp = httpx.post(
        f"http://127.0.0.1:{http_port}/setup/pod",
        json={"css_url": "https://solidcommunity.net"},
        timeout=5,
    )
    assert resp.status_code == 400
    assert "required" in resp.json().get("message", "")


@pytest.mark.asyncio
async def test_post_setup_pod_bad_credentials_returns_error_message(tmp_path):
    """R16.5.2: POST /setup/pod with wrong credentials returns status:error with human message."""
    gw, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.2)

    resp = httpx.post(
        f"http://127.0.0.1:{http_port}/setup/pod",
        json={
            "css_url": "http://127.0.0.1:1",  # nothing listening here
            "email": "nobody@example.com",
            "password": "wrongpassword",
        },
        timeout=10,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "error"
    assert data["message"]
    assert "stack" not in data["message"].lower()


@pytest.mark.asyncio
async def test_get_setup_pod_returns_connected_after_mock_pod(tmp_path):
    """R16.5.1 (partial): GET /setup/pod returns connected: true after _pod_available set."""
    gw, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.2)

    # Simulate a successful pod connection by directly setting gateway state
    gw._pod_available = True
    gw._pod_url = "https://pod.solidcommunity.net/testuser/"

    resp = httpx.get(f"http://127.0.0.1:{http_port}/setup/pod", timeout=5)
    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is True
    assert data["pod_url"] == "https://pod.solidcommunity.net/testuser/"


@pytest.mark.asyncio
async def test_get_setup_pod_connected_after_post(tmp_path):
    """R18.4.2: GET /setup/pod returns connected: true and correct pod_url after POST succeeds."""
    gw, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.2)

    # Stub _connect_css_sync so no real CSS server is needed
    def _fake_connect(css_url, email, password):
        gw._pod_url = "https://pod.solidcommunity.net/testuser/"
        gw._pod_webid = "https://pod.solidcommunity.net/testuser/profile/card#me"

    gw._connect_css_sync = _fake_connect

    post_resp = httpx.post(
        f"http://127.0.0.1:{http_port}/setup/pod",
        json={
            "css_url": "https://solidcommunity.net",
            "email": "test@example.com",
            "password": "s3cr3t",
        },
        timeout=10,
    )
    assert post_resp.status_code == 200
    assert post_resp.json()["status"] == "ok"

    get_resp = httpx.get(f"http://127.0.0.1:{http_port}/setup/pod", timeout=5)
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["connected"] is True
    assert data["pod_url"] == "https://pod.solidcommunity.net/testuser/"


@pytest.mark.asyncio
async def test_my_address_event_includes_gateway_http_url(tmp_path):
    """R16: get_my_address response includes gateway_http_url for wizard use."""
    from proxion_messenger_core.didkey import pub_key_to_did
    gw, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.2)

    ws_port = gw.config.port
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
                    assert "gateway_http_url" in msg
                    assert msg["gateway_http_url"].startswith("http")
                    return
            except asyncio.TimeoutError:
                continue
    pytest.fail("my_address event not received")
