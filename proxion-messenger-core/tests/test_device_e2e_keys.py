"""Per-device E2E key storage + resolution for DM fanout (multi-device slice 5)."""
from __future__ import annotations

import json

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


def _last(ws, type_):
    for call in reversed(ws.send.call_args_list):
        msg = json.loads(call[0][0])
        if msg.get("type") == type_:
            return msg
    return None


@pytest.fixture
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "e2ekeys.db")),
    )


async def _register(gw, ws, did, x25519, cert=None):
    gw.clients.add(ws)
    payload = {"cmd": "register", "did": did, "display_name": "D", "x25519_pub": x25519}
    if cert is not None:
        payload["delegation_cert"] = cert
    await gw.process_command(ws, payload)


@pytest.mark.asyncio
async def test_per_device_keys_are_stored_and_resolved(gateway, noauth_env):
    account = Ed25519PrivateKey.generate()
    device = Ed25519PrivateKey.generate()
    account_did, device_did = _did(account), _did(device)
    cert = issue_device_cert(account, device_did)

    ws_primary = _mock_ws()
    ws_device = _mock_ws()
    await _register(gateway, ws_primary, account_did, "KEY_PRIMARY")
    await _register(gateway, ws_device, device_did, "KEY_DEVICE", cert=cert)

    # A peer resolves every device key for the account.
    ws_peer = _mock_ws()
    await _register(gateway, ws_peer, _did(Ed25519PrivateKey.generate()), "KEY_PEER")
    ws_peer.send.reset_mock()
    await gateway.process_command(ws_peer, {"cmd": "get_peer_device_keys", "peer_webid": account_did})

    resp = _last(ws_peer, "peer_device_keys")
    assert resp is not None
    by_device = {d["device_id"]: d["pub_b64u"] for d in resp["devices"]}
    # Both the primary (device_id == account_did) and the delegated device appear.
    assert by_device.get(account_did) == "KEY_PRIMARY"
    assert by_device.get(device_did) == "KEY_DEVICE"


@pytest.mark.asyncio
async def test_key_rotation_updates_in_place(gateway, noauth_env):
    account = Ed25519PrivateKey.generate()
    account_did = _did(account)
    ws1 = _mock_ws()
    await _register(gateway, ws1, account_did, "KEY_V1")
    # Same device re-registers with a new key (reconnect / rotation).
    ws2 = _mock_ws()
    await _register(gateway, ws2, account_did, "KEY_V2")

    keys = gateway._store.list_device_e2e_keys(account_did)
    assert len(keys) == 1  # same device_id → one row, updated
    assert keys[0]["pub_b64u"] == "KEY_V2"


@pytest.mark.asyncio
async def test_unknown_peer_returns_empty(gateway, noauth_env):
    ws = _mock_ws()
    await _register(gateway, ws, _did(Ed25519PrivateKey.generate()), "K")
    ws.send.reset_mock()
    await gateway.process_command(ws, {"cmd": "get_peer_device_keys", "peer_webid": "did:key:zNobody"})
    resp = _last(ws, "peer_device_keys")
    assert resp is not None and resp["devices"] == []
