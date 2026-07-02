"""Security hardening for multi-device: key clobber + real revocation.

Covers three review findings:
1. A delegated device's register must NOT overwrite the account's default E2E
   key (peers on the single-send path would encrypt to the wrong device).
2. Revoking a delegated device must actually revoke it — deleting the registry
   row alone leaves its delegation cert valid, so it could just reconnect.
3. Revoking a device must close that device's live session, and the account's
   own primary identity must not be revocable through this path.
"""
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
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "revoke.db")),
    )


async def _register(gw, ws, did, x25519=None, cert=None):
    gw.clients.add(ws)
    p = {"cmd": "register", "did": did, "display_name": "D"}
    if x25519 is not None:
        p["x25519_pub"] = x25519
    if cert is not None:
        p["delegation_cert"] = cert
    await gw.process_command(ws, p)


def _paired_account():
    account = Ed25519PrivateKey.generate()
    device = Ed25519PrivateKey.generate()
    return account, device, _did(account), _did(device), issue_device_cert(account, _did(device))


# ── Finding 1: account key clobber ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_delegated_device_does_not_clobber_account_key(gateway, noauth_env):
    _, _, account_did, device_did, cert = _paired_account()
    ws_primary = _mock_ws()
    ws_device = _mock_ws()
    await _register(gateway, ws_primary, account_did, x25519="KEY_PRIMARY")
    await _register(gateway, ws_device, device_did, x25519="KEY_DEVICE", cert=cert)

    # The account-level (single-send) key must still be the primary's.
    assert gateway._store.get_x25519_pub(account_did) == "KEY_PRIMARY"
    # Both devices are still individually resolvable for fanout.
    keys = {d["device_id"]: d["pub_b64u"] for d in gateway._store.list_device_e2e_keys(account_did)}
    assert keys == {account_did: "KEY_PRIMARY", device_did: "KEY_DEVICE"}


# ── Findings 2+3: revocation actually revokes ───────────────────────────────

@pytest.mark.asyncio
async def test_revoked_device_cannot_reconnect_with_its_cert(gateway, noauth_env):
    _, _, account_did, device_did, cert = _paired_account()
    ws_primary = _mock_ws()
    ws_device = _mock_ws()
    await _register(gateway, ws_primary, account_did, x25519="KP")
    await _register(gateway, ws_device, device_did, x25519="KD", cert=cert)

    await gateway.process_command(ws_primary, {
        "cmd": "revoke_device_and_rekey", "device_id": device_did,
    })
    assert _last(ws_primary, "device_revoked_ack") is not None
    # Revocation is recorded in memory AND persisted.
    assert device_did in gateway._revoked_dids
    assert device_did in gateway._store.get_revoked_dids()
    # Its fanout key is gone — peers stop encrypting copies to it.
    ids = [d["device_id"] for d in gateway._store.list_device_e2e_keys(account_did)]
    assert device_did not in ids
    # The revoked device's live session was cut, not just notified.
    assert _last(ws_device, "session_revoked") is not None
    assert ws_device.close.called

    # Reconnect attempt with the still-TTL-valid cert is refused.
    ws_again = _mock_ws()
    await _register(gateway, ws_again, device_did, x25519="KD", cert=cert)
    assert ws_again not in gateway._client_webids
    ws_again.close.assert_called()  # closed as identity_revoked


@pytest.mark.asyncio
async def test_primary_identity_cannot_be_revoked_as_device(gateway, noauth_env):
    _, _, account_did, device_did, cert = _paired_account()
    ws_primary = _mock_ws()
    ws_device = _mock_ws()
    await _register(gateway, ws_primary, account_did, x25519="KP")
    await _register(gateway, ws_device, device_did, x25519="KD", cert=cert)
    # The primary registered itself under device_id == account_did.
    ws_primary.send.reset_mock()
    await gateway.process_command(ws_primary, {
        "cmd": "revoke_device_and_rekey", "device_id": account_did,
    })
    err = _last(ws_primary, "error")
    assert err is not None and err["message"] == "cannot_revoke_primary"
    assert account_did not in gateway._revoked_dids
