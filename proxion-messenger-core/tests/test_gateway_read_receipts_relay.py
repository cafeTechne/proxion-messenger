"""Tests for relay read receipts — R10.3.

R10.3.1: mark_read with a cert DM thread attempts to POST to /relay/receipt on the peer gateway.
R10.3.2: POST /relay/receipt delivers a read_receipt WebSocket event to the recipient.
"""
import asyncio
import json
import socket
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.didkey import pub_key_to_did


def _make_gateway(tmp_path, **cfg_kwargs):
    agent = AgentState.generate()
    cfg = GatewayConfig(db_path=str(tmp_path / "gw.db"), host="127.0.0.1", **cfg_kwargs)
    gw = ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=cfg, read_state=ReadState())
    return gw, agent


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    return ws


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── R10.3.1: mark_read with cert DM attempts relay receipt POST ────────────


@pytest.mark.asyncio
async def test_mark_read_attempts_relay_receipt_post(tmp_path):
    """R10.3.1: mark_read on a cert DM thread POSTs a receipt to the peer's gateway."""
    gw, agent = _make_gateway(tmp_path)

    peer_priv = Ed25519PrivateKey.generate()
    peer_pub = peer_priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    peer_did = pub_key_to_did(peer_pub)

    cert_id = "cert-mark-read-test"
    gw._store.save_relationship(
        {
            "certificate_id": cert_id,
            "issuer": agent.identity_pub_bytes.hex(),
            "subject": peer_pub.hex(),
            "signature": "dummy",
        },
        peer_did=peer_did,
    )
    peer_gw_url = "http://peer-gateway:9000"
    gw._store.save_peer_gateway(peer_did, peer_gw_url)
    gw._peer_gateway_urls[peer_did] = peer_gw_url

    caller_did = pub_key_to_did(agent.identity_pub_bytes)
    ws = _mock_ws()
    gw.clients.add(ws)
    gw._client_webids[ws] = caller_did

    posted_urls = []

    async def fake_async_safe_post(url, payload, **kwargs):
        posted_urls.append(url)
        return True

    with patch("proxion_messenger_core.network.async_safe_post", side_effect=fake_async_safe_post):
        await gw._handle_mark_read(ws, {
            "thread_id": cert_id,
            "message_id": "msg-abc",
        })

    assert any("/relay/receipt" in url for url in posted_urls), (
        f"Expected /relay/receipt POST but got: {posted_urls}"
    )


@pytest.mark.asyncio
async def test_mark_read_no_receipt_without_peer_gateway(tmp_path):
    """mark_read does not attempt /relay/receipt when no peer gateway URL is known."""
    gw, agent = _make_gateway(tmp_path)

    peer_priv = Ed25519PrivateKey.generate()
    peer_pub = peer_priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    peer_did = pub_key_to_did(peer_pub)

    cert_id = "cert-no-gw"
    gw._store.save_relationship(
        {
            "certificate_id": cert_id,
            "issuer": agent.identity_pub_bytes.hex(),
            "subject": peer_pub.hex(),
            "signature": "dummy",
        },
        peer_did=peer_did,
    )
    # No peer gateway recorded

    caller_did = pub_key_to_did(agent.identity_pub_bytes)
    ws = _mock_ws()
    gw.clients.add(ws)
    gw._client_webids[ws] = caller_did

    posted_urls = []

    def fake_httpx_post(url, **kwargs):
        posted_urls.append(url)
        return MagicMock(status_code=200)

    with patch("httpx.post", side_effect=fake_httpx_post):
        await gw._handle_mark_read(ws, {"thread_id": cert_id, "message_id": "msg-xyz"})

    assert posted_urls == []


# ── R10.3.2: POST /relay/receipt delivers read_receipt WebSocket event ─────


pytest.importorskip("websockets")
import websockets
from gwharness import start_gateway as _serve_gw


def _start_gateway(agent, ws_port, http_port, db_path):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.readstate import ReadState

    cfg = GatewayConfig(
        host="127.0.0.1", port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}", db_path=db_path,
    )
    gw = ProxionGateway(agent, {}, {}, cfg, ReadState())
    # Raises if the gateway fails to start or never accepts a connection, and
    # registers it for shutdown after the test (see tests/gwharness.py).
    handle = _serve_gw(gw, ws_port, http_port)
    return gw, handle.thread, handle.loop, handle.ready


@pytest.mark.asyncio
async def test_relay_receipt_endpoint_delivers_read_receipt_event(tmp_path):
    """R10.3.2: POST /relay/receipt fans out a read_receipt WebSocket event to the recipient."""
    import httpx

    ws_port = _free_port()
    http_port = _free_port()

    agent = AgentState.generate()
    gw, _, _, ready = _start_gateway(agent, ws_port, http_port, str(tmp_path / "gw.db"))
    assert ready.wait(timeout=5), "Gateway failed to start"
    await asyncio.sleep(0.2)

    recipient_did = pub_key_to_did(agent.identity_pub_bytes)

    async with websockets.connect(f"ws://127.0.0.1:{ws_port}") as conn:
        await conn.send(json.dumps({"cmd": "register", "did": recipient_did}))
        # Drain registration events
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                await asyncio.wait_for(conn.recv(), timeout=0.3)
            except asyncio.TimeoutError:
                break

        # Build a real sender identity so we can sign the receipt
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        from proxion_messenger_core.relay import sign_relay_message
        from datetime import datetime, timezone
        sender_priv = Ed25519PrivateKey.generate()
        sender_pub = sender_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        sender_did = pub_key_to_did(sender_pub)
        ts = datetime.now(timezone.utc).isoformat()
        sig = sign_relay_message(sender_priv, sender_did, recipient_did, "msg-receipt-test", "", ts)

        # POST a signed relay receipt to the gateway
        resp = httpx.post(
            f"http://127.0.0.1:{http_port}/relay/receipt",
            json={
                "from_did": sender_did,
                "to_did": recipient_did,
                "message_id": "msg-receipt-test",
                "thread_id": "cert-receipt-test",
                "timestamp": ts,
                "signature": sig,
            },
            timeout=5,
        )
        assert resp.status_code == 200, f"POST /relay/receipt failed: {resp.text}"

        # The registered recipient should receive a read_receipt event
        receipt_event = None
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(conn.recv(), timeout=0.3)
                msg = json.loads(raw)
                if msg.get("type") == "read_receipt":
                    receipt_event = msg
                    break
            except asyncio.TimeoutError:
                continue

    assert receipt_event is not None, "Did not receive read_receipt event"
    assert receipt_event["message_id"] == "msg-receipt-test"
    assert receipt_event["from_did"] == sender_did
    assert receipt_event["to_did"] == recipient_did
