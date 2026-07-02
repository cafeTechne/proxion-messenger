"""Gateway acceptance of delegated devices (multi-device slice 2).

A secondary device authenticates with its own device_did plus a delegation_cert
the account signed; the gateway admits the connection AS the account_did so all
account-scoped routing (rooms/DMs/presence) is shared across devices.
"""
from __future__ import annotations

import base64
import json
import uuid

import pytest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.device_cert import issue_device_cert
from proxion_messenger_core.didkey import pub_key_to_did


def _did(priv: Ed25519PrivateKey) -> str:
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


@pytest.fixture
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


@pytest.fixture
def gateway(tmp_path):
    a = AgentState.generate()
    return ProxionGateway(
        agent=a, dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "deleg.db")),
    )


async def _register(gw, ws, did, cert=None, name="Dev"):
    gw.clients.add(ws)
    payload = {"cmd": "register", "did": did, "display_name": name}
    if cert is not None:
        payload["delegation_cert"] = cert
    await gw.process_command(ws, payload)


def _last(ws, type_):
    for call in reversed(ws.send.call_args_list):
        msg = json.loads(call[0][0])
        if msg.get("type") == type_:
            return msg
    return None


@pytest.mark.asyncio
async def test_delegated_device_adopts_account_identity(gateway, noauth_env):
    account = Ed25519PrivateKey.generate()
    device = Ed25519PrivateKey.generate()
    account_did, device_did = _did(account), _did(device)
    cert = issue_device_cert(account, device_did)

    ws_primary = _mock_ws()
    ws_device = _mock_ws()
    await _register(gateway, ws_primary, account_did)
    await _register(gateway, ws_device, device_did, cert=cert)

    # Both sessions are keyed under the account identity.
    assert gateway._client_webids[ws_primary] == account_did
    assert gateway._client_webids[ws_device] == account_did
    assert {ws_primary, ws_device} <= gateway._webid_sockets.get(account_did, set())
    # The physical device DID is remembered only for the delegated session.
    assert gateway._session_device_did.get(ws_device) == device_did
    assert ws_primary not in gateway._session_device_did


@pytest.mark.asyncio
async def test_room_message_reaches_all_account_devices(gateway, noauth_env):
    account = Ed25519PrivateKey.generate()
    device = Ed25519PrivateKey.generate()
    account_did, device_did = _did(account), _did(device)
    cert = issue_device_cert(account, device_did)

    ws_primary = _mock_ws()
    ws_device = _mock_ws()
    ws_bob = _mock_ws()
    await _register(gateway, ws_primary, account_did)
    await _register(gateway, ws_device, device_did, cert=cert)
    await _register(gateway, ws_bob, _did(Ed25519PrivateKey.generate()))

    room_id = "room-deleg"
    gateway._local_rooms[room_id] = {
        "members": {ws_primary, ws_device, ws_bob}, "messages": [], "history_mode": "none",
    }
    ws_primary.send.reset_mock()
    ws_device.send.reset_mock()
    await gateway.process_command(ws_bob, {
        "cmd": "send_room", "room_id": room_id,
        "content": "hi all my devices", "message_id": str(uuid.uuid4()),
    })
    assert ws_primary.send.called, "primary device should receive"
    assert ws_device.send.called, "delegated device should receive"


@pytest.mark.asyncio
async def test_invalid_delegation_cert_is_rejected(gateway, noauth_env):
    account = Ed25519PrivateKey.generate()
    device = Ed25519PrivateKey.generate()
    device_did = _did(device)
    cert = issue_device_cert(account, device_did)
    cert["signature"] = base64.b64encode(b"\x00" * 64).decode()  # forge

    ws = _mock_ws()
    await _register(gateway, ws, device_did, cert=cert)

    fail = _last(ws, "auth_failed")
    assert fail is not None and fail["reason"] == "invalid_delegation"
    assert ws not in gateway._client_webids


@pytest.mark.asyncio
async def test_cert_for_other_device_cannot_be_replayed(gateway, noauth_env):
    """A cert issued for device A must not admit a session claiming device B."""
    account = Ed25519PrivateKey.generate()
    device_a = Ed25519PrivateKey.generate()
    device_b = Ed25519PrivateKey.generate()
    cert_for_a = issue_device_cert(account, _did(device_a))

    ws = _mock_ws()
    # Connection claims device_b but presents A's cert.
    await _register(gateway, ws, _did(device_b), cert=cert_for_a)

    fail = _last(ws, "auth_failed")
    assert fail is not None and fail["reason"] == "invalid_delegation"
    assert ws not in gateway._client_webids


@pytest.mark.asyncio
async def test_auth_mode_challenge_then_delegation(gateway, monkeypatch):
    """Full auth path: prove device_did via challenge, then adopt account_did."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
    account = Ed25519PrivateKey.generate()
    device = Ed25519PrivateKey.generate()
    account_did, device_did = _did(account), _did(device)
    cert = issue_device_cert(account, device_did)

    ws = _mock_ws()
    gateway.clients.add(ws)
    await gateway.process_command(ws, {
        "cmd": "register", "did": device_did, "display_name": "Phone",
        "delegation_cert": cert,
    })
    challenge = _last(ws, "auth_challenge")
    assert challenge is not None, "should be challenged for the device key"
    nonce = challenge["nonce"]

    sig = device.sign(nonce.encode())
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    ws.send.reset_mock()
    await gateway.process_command(ws, {"cmd": "auth_response", "nonce": nonce, "signature": sig_b64})

    # Admitted as the account, not the device.
    assert gateway._client_webids.get(ws) == account_did
    assert gateway._session_device_did.get(ws) == device_did
