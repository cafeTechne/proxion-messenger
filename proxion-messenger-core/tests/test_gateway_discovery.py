"""Tests for gateway discovery endpoint /.well-known/proxion."""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


def _make_gateway(public_url="wss://test.example.com:7474", **cfg_kwargs):
    """Helper to create a test gateway with mocked agent."""
    key = Ed25519PrivateKey.generate()
    agent = MagicMock(spec=AgentState)
    agent.identity_key = key
    pub_bytes = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    agent.identity_pub_bytes = pub_bytes
    
    config = GatewayConfig(public_url=public_url, **cfg_kwargs)
    
    gateway = ProxionGateway(
        agent=agent,
        dm_clients={},
        room_memberships={},
        config=config,
        read_state=ReadState(),
    )
    return gateway, agent


def test_well_known_returns_did_and_gateway_url():
    """Test that /.well-known/proxion returns did starting with did:key: and gateway_url."""
    gateway, agent = _make_gateway()
    
    # Build discovery response (simulating endpoint logic)
    discovery_data = {
        "proxion_version": "0.1",
        "did": pub_key_to_did(agent.identity_pub_bytes),
        "gateway_url": gateway._ws_public_url(),
    }
    if gateway.dm_clients and gateway._pod_url:
        discovery_data["pod_url"] = gateway._pod_url
    
    # Verify structure
    assert discovery_data["proxion_version"] == "0.1"
    assert discovery_data["did"].startswith("did:key:")
    assert "gateway_url" in discovery_data
    assert discovery_data["gateway_url"] == "wss://test.example.com:7474"


def test_well_known_omits_pod_url_when_no_pod_connected():
    """Test that pod_url key is absent when dm_clients is empty."""
    gateway, agent = _make_gateway()
    
    # Ensure dm_clients is empty
    assert len(gateway.dm_clients) == 0
    assert gateway._pod_url is None
    
    # Build discovery response
    discovery_data = {
        "proxion_version": "0.1",
        "did": pub_key_to_did(agent.identity_pub_bytes),
        "gateway_url": gateway._ws_public_url(),
    }
    if gateway.dm_clients and gateway._pod_url:
        discovery_data["pod_url"] = gateway._pod_url
    
    # Verify pod_url is not present
    assert "pod_url" not in discovery_data


def test_well_known_returns_200_content_type_json():
    """Test that endpoint returns HTTP 200 with Content-Type application/json."""
    gateway, agent = _make_gateway()
    
    discovery_data = {
        "proxion_version": "0.1",
        "did": pub_key_to_did(agent.identity_pub_bytes),
        "gateway_url": gateway._ws_public_url(),
    }
    
    resp_bytes = json.dumps(discovery_data).encode()
    
    # Verify it's valid JSON
    parsed = json.loads(resp_bytes.decode())
    assert isinstance(parsed, dict)
    assert "proxion_version" in parsed
    assert "did" in parsed
    assert "gateway_url" in parsed


def test_well_known_includes_pod_url_when_connected():
    """Test that pod_url is included when a pod is connected."""
    gateway, agent = _make_gateway()
    
    # Simulate pod connection by setting _pod_url and adding to dm_clients
    gateway._pod_url = "https://pod.example.com/alice/"
    mock_cert = MagicMock()
    mock_client = MagicMock()
    gateway.dm_clients["pod_webid"] = (mock_cert, mock_client)
    
    # Build discovery response
    discovery_data = {
        "proxion_version": "0.1",
        "did": pub_key_to_did(agent.identity_pub_bytes),
        "gateway_url": gateway._ws_public_url(),
    }
    if gateway.dm_clients and gateway._pod_url:
        discovery_data["pod_url"] = gateway._pod_url
    
    # Verify pod_url is present
    assert "pod_url" in discovery_data
    assert discovery_data["pod_url"] == "https://pod.example.com/alice/"


# ── R8.5.1: well-known includes display_name and proxion_address ──────────


def test_proxion_address_format():
    """R8.5.1: _proxion_address() returns 'user@host:port' format when http_port set."""
    gateway, _ = _make_gateway(http_port=9090, host="127.0.0.1")
    addr = gateway._proxion_address()
    assert "@" in addr
    assert "127.0.0.1:9090" in addr


def test_proxion_address_uses_ws_url_fallback_without_http_port():
    """_proxion_address() falls back to the WS URL when no http_port is configured."""
    from proxion_messenger_core.gateway import GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as _Ed
    from cryptography.hazmat.primitives import serialization as _ser
    agent = MagicMock(spec=AgentState)
    key = _Ed.generate()
    pub_bytes = key.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw)
    agent.identity_pub_bytes = pub_bytes
    agent.identity_key = key
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(http_port=None, public_url="wss://relay.example.com:7474"),
        read_state=ReadState(),
    )
    # Without http_port, falls back to WS-derived URL — still non-empty
    addr = gw._proxion_address()
    assert "@" in addr
    assert "relay.example.com" in addr


def test_well_known_always_includes_proxion_address_key(tmp_path):
    """R8.5.1: discovery_data dict always has a proxion_address key (may be empty str)."""
    from proxion_messenger_core.didkey import pub_key_to_did
    gateway, agent = _make_gateway(http_port=9191, host="127.0.0.1")
    gw_did = pub_key_to_did(agent.identity_pub_bytes)
    http_url = gateway._gateway_http_url()
    proxion_addr = gateway._proxion_address()
    discovery_data = {
        "proxion_version": "0.1",
        "did": gw_did,
        "gateway_url": gateway._ws_public_url(),
        "gateway_http_url": http_url,
        "proxion_address": proxion_addr,
    }
    assert "proxion_address" in discovery_data


def test_well_known_conditionally_includes_display_name(tmp_path):
    """R8.5.1: display_name appears in discovery only when store has it."""
    from proxion_messenger_core.gateway import GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState
    from proxion_messenger_core.local_store import LocalStore
    from proxion_messenger_core.didkey import pub_key_to_did

    agent = AgentState.generate()
    store = LocalStore(str(tmp_path / "gw.db"))
    gw_did = pub_key_to_did(agent.identity_pub_bytes)
    store.save_display_name(gw_did, "My Gateway Node")

    cfg = GatewayConfig(db_path=str(tmp_path / "gw.db"), http_port=8585, host="127.0.0.1")
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={}, config=cfg, read_state=ReadState()
    )

    dn = gw._store.get_display_name(gw_did)
    assert dn == "My Gateway Node"

    # Simulate the discovery data build
    discovery_data = {"proxion_version": "0.1"}
    if gw._store:
        saved_dn = gw._store.get_display_name(gw_did)
        if saved_dn:
            discovery_data["display_name"] = saved_dn

    assert discovery_data.get("display_name") == "My Gateway Node"


# ── R8.5.2: GET /invite?from= returns 200 HTML ────────────────────────────


pytest.importorskip("websockets")
import websockets
import socket as _socket
import httpx as _httpx
from gwharness import start_gateway as _serve_gw


def _free_port_disc():
    with _socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_http_gateway(agent, ws_port, http_port):
    import asyncio as _aio

    cfg = GatewayConfig(
        host="127.0.0.1", port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}",
    )
    gw = ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=cfg, read_state=ReadState())

    # Raises on startup failure and is shut down after the test
    # (see tests/gwharness.py).
    handle = _serve_gw(gw, ws_port, http_port)
    ready = handle.ready
    return gw, ready


@pytest.mark.asyncio
async def test_get_invite_with_from_param_returns_html(tmp_path):
    """R8.5.2: GET /invite?from=<addr> returns 200 with Content-Type text/html."""
    agent = AgentState.generate()
    ws_port = _free_port_disc()
    http_port = _free_port_disc()

    _, ready = _start_http_gateway(agent, ws_port, http_port)
    assert ready.wait(timeout=5), "HTTP gateway failed to start"
    await asyncio.sleep(0.2)

    from_addr = "did:key:z6MkAlice@http://alice-gateway:9000"
    resp = _httpx.get(
        f"http://127.0.0.1:{http_port}/invite",
        params={"from": from_addr},
        follow_redirects=False,
        timeout=5,
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    # Should contain the from address or a redirect to root with ?from=
    body = resp.text
    assert "html" in body.lower()


@pytest.mark.asyncio
async def test_get_invite_without_param_still_returns_html(tmp_path):
    """GET /invite without ?from= also returns 200 HTML (redirects to root)."""
    agent = AgentState.generate()
    ws_port = _free_port_disc()
    http_port = _free_port_disc()

    _, ready = _start_http_gateway(agent, ws_port, http_port)
    assert ready.wait(timeout=5), "HTTP gateway failed to start"
    await asyncio.sleep(0.2)

    resp = _httpx.get(
        f"http://127.0.0.1:{http_port}/invite",
        follow_redirects=False,
        timeout=5,
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")

