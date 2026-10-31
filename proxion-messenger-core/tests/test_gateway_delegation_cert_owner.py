"""The gateway delegation cert is issued only to the gateway owner's account.

The cert says "this browser key speaks for the gateway identity". A federated
contact knows us by the gateway did, so a call signed by a key holding the cert
shows as Verified owner on the far side. A guest account on a shared gateway
must never get one.
"""
from __future__ import annotations

import asyncio
import base64
import json

import pytest
import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from unittest.mock import AsyncMock

from gwharness import start_gateway
from proxion_messenger_core.device_cert import issue_device_cert, verify_device_cert
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState


def _did(priv: Ed25519PrivateKey) -> str:
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _make_gateway(tmp_path) -> ProxionGateway:
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", port=0, http_port=None,
                             db_path=str(tmp_path / "gwcert.db")),
    )


@pytest.fixture
def forced_auth_gateway(tmp_path):
    gw = _make_gateway(tmp_path)
    gw._force_auth = True   # as a live public tunnel does
    handle = start_gateway(gw, 0, 0, serve_http=False)
    return gw, f"ws://127.0.0.1:{handle.ws_port}"


async def _register(url: str, key: Ed25519PrivateKey, cert=None) -> dict:
    """Register *key*'s did (optionally as a linked device) and return 'registered'."""
    payload = {"cmd": "register", "did": _did(key), "display_name": "x"}
    if cert is not None:
        payload["delegation_cert"] = cert
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps(payload))
        for _ in range(10):
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
            if ev.get("type") == "auth_challenge":
                sig = key.sign(ev["nonce"].encode())
                await ws.send(json.dumps({
                    "cmd": "auth_response", "nonce": ev["nonce"],
                    "signature": base64.urlsafe_b64encode(sig).rstrip(b"=").decode(),
                }))
            elif ev.get("type") == "registered":
                return ev
            elif ev.get("type") == "auth_failed":
                raise AssertionError(f"auth failed: {ev}")
    raise AssertionError("no registered event")


@pytest.mark.asyncio
async def test_guest_account_gets_no_gateway_cert(forced_auth_gateway):
    _gw, url = forced_auth_gateway
    reg = await _register(url, Ed25519PrivateKey.generate())
    assert "gateway_delegation_cert" not in reg


@pytest.mark.asyncio
async def test_owner_account_gets_gateway_cert(forced_auth_gateway):
    gw, url = forced_auth_gateway
    owner_did = pub_key_to_did(gw.agent.identity_pub_bytes)
    reg = await _register(url, gw.agent.identity_key)
    cert = reg.get("gateway_delegation_cert")
    assert cert and verify_device_cert(
        cert, expected_device_did=owner_did, expected_account_did=owner_did) == owner_did


@pytest.mark.asyncio
async def test_owner_linked_device_gets_gateway_cert(forced_auth_gateway):
    gw, url = forced_auth_gateway
    owner_did = pub_key_to_did(gw.agent.identity_pub_bytes)
    device = Ed25519PrivateKey.generate()
    reg = await _register(url, device, cert=issue_device_cert(gw.agent.identity_key, _did(device)))
    cert = reg.get("gateway_delegation_cert")
    assert cert and verify_device_cert(
        cert, expected_device_did=_did(device), expected_account_did=owner_did) == owner_did


@pytest.mark.asyncio
async def test_guest_linked_device_gets_no_gateway_cert(forced_auth_gateway):
    _gw, url = forced_auth_gateway
    guest, device = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    reg = await _register(url, device, cert=issue_device_cert(guest, _did(device)))
    assert "gateway_delegation_cert" not in reg


def _mock_ws():
    ws = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 40000)
    ws.request = None
    ws.request_headers = {}   # a direct, non-proxied connection
    return ws


def _registered(ws) -> dict:
    for call in ws.send.call_args_list:
        msg = json.loads(call[0][0])
        if msg.get("type") == "registered":
            return msg
    return {}


@pytest.mark.asyncio
async def test_local_owner_linked_device_keeps_cert_once_auth_is_forced(tmp_path, monkeypatch):
    """The desktop browser registers under its own session did on a loopback
    gateway (auth off) and is the owner. Its phone, linked to that account, later
    connects over a tunnel (auth forced) and must still get the cert; a guest
    connecting the same way must not."""
    monkeypatch.delenv("PROXION_REQUIRE_AUTH", raising=False)
    gw = _make_gateway(tmp_path)
    browser, phone, guest = (Ed25519PrivateKey.generate() for _ in range(3))

    ws_local = _mock_ws()
    gw.clients.add(ws_local)
    await gw.process_command(ws_local, {"cmd": "register", "did": _did(browser)})
    assert _registered(ws_local).get("gateway_delegation_cert")

    gw._force_auth = True
    ws_phone, ws_guest = _mock_ws(), _mock_ws()
    for ws in (ws_phone, ws_guest):
        gw.clients.add(ws)
        gw._auth_verified.add(ws)   # stands in for a passed auth challenge
    await gw.process_command(ws_phone, {
        "cmd": "register", "did": _did(phone),
        "delegation_cert": issue_device_cert(browser, _did(phone)),
    })
    await gw.process_command(ws_guest, {"cmd": "register", "did": _did(guest)})
    assert gw._client_webids[ws_phone] == _did(browser)
    assert _registered(ws_phone).get("gateway_delegation_cert")
    assert _registered(ws_guest) and not _registered(ws_guest).get("gateway_delegation_cert")
