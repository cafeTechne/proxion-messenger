"""Gateway pairing relay: primary <-> new-device delegation-cert exchange (slice 3)."""
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
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "pair.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


@pytest.mark.asyncio
async def test_full_pairing_flow_then_delegated_register(gateway, noauth_env):
    account = Ed25519PrivateKey.generate()
    device = Ed25519PrivateKey.generate()
    account_did, device_did = _did(account), _did(device)

    ws_primary = _mock_ws()
    ws_device = _mock_ws()
    await _register(gateway, ws_primary, account_did)

    # 1. Primary starts pairing.
    await gateway.process_command(ws_primary, {"cmd": "pair_start"})
    started = _last(ws_primary, "pairing_started")
    assert started is not None
    code = started["pairing_code"]

    # 2. New (unregistered) device submits its device_did.
    await gateway.process_command(ws_device, {
        "cmd": "pair_submit", "pairing_code": code, "device_did": device_did,
    })
    req = _last(ws_primary, "pairing_request")
    assert req is not None and req["device_did"] == device_did
    submitted = _last(ws_device, "pairing_submitted")
    assert submitted is not None
    # Both sides compute the same safety code from the device_did.
    assert req["safety_code"] == submitted["safety_code"]

    # 3. Primary signs a cert for that device and approves.
    cert = issue_device_cert(account, device_did)
    await gateway.process_command(ws_primary, {
        "cmd": "pair_approve", "pairing_code": code, "delegation_cert": cert,
    })
    assert _last(ws_primary, "pairing_approve_ack") is not None
    approved = _last(ws_device, "pairing_approved")
    assert approved is not None
    assert approved["account_did"] == account_did
    assert approved["delegation_cert"] == cert
    # Session is single-use / consumed.
    assert code not in gateway._pairing_sessions

    # 4. The new device uses the relayed cert to register as a delegated device.
    await gateway.process_command(ws_device, {
        "cmd": "register", "did": device_did, "display_name": "Phone",
        "delegation_cert": approved["delegation_cert"],
    })
    assert gateway._client_webids.get(ws_device) == account_did
    assert gateway._session_device_did.get(ws_device) == device_did


async def _run_to_approve(gateway, ws_primary, ws_device, account, device_did, extra_approve=None):
    await gateway.process_command(ws_primary, {"cmd": "pair_start"})
    code = _last(ws_primary, "pairing_started")["pairing_code"]
    await gateway.process_command(ws_device, {
        "cmd": "pair_submit", "pairing_code": code, "device_did": device_did})
    cert = issue_device_cert(account, device_did)
    cmd = {"cmd": "pair_approve", "pairing_code": code, "delegation_cert": cert}
    if extra_approve:
        cmd.update(extra_approve)
    await gateway.process_command(ws_primary, cmd)
    return _last(ws_device, "pairing_approved")


@pytest.mark.asyncio
async def test_pair_approve_relays_history_bundle(gateway, noauth_env):
    """E5 slice 1: the primary's DM-history bundle rides the pairing_approved."""
    account = Ed25519PrivateKey.generate()
    device = Ed25519PrivateKey.generate()
    ws_primary, ws_device = _mock_ws(), _mock_ws()
    await _register(gateway, ws_primary, _did(account))
    bundle = [{"message_id": "m1", "thread_id": "t1", "content": "hi", "timestamp": "2024-01-01"}]
    approved = await _run_to_approve(gateway, ws_primary, ws_device, account, _did(device),
                                     extra_approve={"history_bundle": bundle})
    assert approved is not None
    assert approved.get("history_bundle") == bundle


@pytest.mark.asyncio
async def test_pair_approve_drops_oversized_history_bundle(gateway, noauth_env):
    """An oversized bundle must be dropped so the cert still gets through."""
    account = Ed25519PrivateKey.generate()
    device = Ed25519PrivateKey.generate()
    ws_primary, ws_device = _mock_ws(), _mock_ws()
    await _register(gateway, ws_primary, _did(account))
    huge = [{"message_id": f"m{i}", "thread_id": "t", "content": "x" * 1024,
             "timestamp": "2024"} for i in range(2000)]  # > 1 MiB
    approved = await _run_to_approve(gateway, ws_primary, ws_device, account, _did(device),
                                     extra_approve={"history_bundle": huge})
    assert approved is not None
    assert approved["delegation_cert"] is not None      # cert still delivered
    assert "history_bundle" not in approved             # oversized bundle dropped


@pytest.mark.asyncio
async def test_pair_submit_unknown_code(gateway, noauth_env):
    ws_device = _mock_ws()
    await gateway.process_command(ws_device, {
        "cmd": "pair_submit", "pairing_code": "nope", "device_did": _did(Ed25519PrivateKey.generate()),
    })
    inv = _last(ws_device, "pairing_invalid")
    assert inv is not None and inv["reason"] == "no_such_session"


@pytest.mark.asyncio
async def test_pair_submit_is_single_claim(gateway, noauth_env):
    account = Ed25519PrivateKey.generate()
    ws_primary = _mock_ws()
    await _register(gateway, ws_primary, _did(account))
    await gateway.process_command(ws_primary, {"cmd": "pair_start"})
    code = _last(ws_primary, "pairing_started")["pairing_code"]

    ws_a = _mock_ws()
    ws_b = _mock_ws()
    await gateway.process_command(ws_a, {
        "cmd": "pair_submit", "pairing_code": code, "device_did": _did(Ed25519PrivateKey.generate())})
    assert _last(ws_a, "pairing_submitted") is not None
    # A second device racing the same code is rejected (status no longer 'started').
    await gateway.process_command(ws_b, {
        "cmd": "pair_submit", "pairing_code": code, "device_did": _did(Ed25519PrivateKey.generate())})
    assert _last(ws_b, "pairing_invalid") is not None


@pytest.mark.asyncio
async def test_pair_approve_rejects_cert_for_wrong_device(gateway, noauth_env):
    account = Ed25519PrivateKey.generate()
    real_device = Ed25519PrivateKey.generate()
    other_device = Ed25519PrivateKey.generate()
    ws_primary = _mock_ws()
    ws_device = _mock_ws()
    await _register(gateway, ws_primary, _did(account))
    await gateway.process_command(ws_primary, {"cmd": "pair_start"})
    code = _last(ws_primary, "pairing_started")["pairing_code"]
    await gateway.process_command(ws_device, {
        "cmd": "pair_submit", "pairing_code": code, "device_did": _did(real_device)})

    # Cert for the wrong device.
    bad_cert = issue_device_cert(account, _did(other_device))
    await gateway.process_command(ws_primary, {
        "cmd": "pair_approve", "pairing_code": code, "delegation_cert": bad_cert})
    err = _last(ws_primary, "error")
    assert err is not None and err["message"] == "invalid_cert"
    # Nothing relayed to the device; session still pending.
    assert _last(ws_device, "pairing_approved") is None


@pytest.mark.asyncio
async def test_pair_approve_only_by_owning_primary(gateway, noauth_env):
    account = Ed25519PrivateKey.generate()
    device = Ed25519PrivateKey.generate()
    ws_primary = _mock_ws()
    ws_device = _mock_ws()
    ws_stranger = _mock_ws()
    await _register(gateway, ws_primary, _did(account))
    await _register(gateway, ws_stranger, _did(Ed25519PrivateKey.generate()))
    await gateway.process_command(ws_primary, {"cmd": "pair_start"})
    code = _last(ws_primary, "pairing_started")["pairing_code"]
    await gateway.process_command(ws_device, {
        "cmd": "pair_submit", "pairing_code": code, "device_did": _did(device)})

    # A different authenticated session tries to approve.
    cert = issue_device_cert(account, _did(device))
    await gateway.process_command(ws_stranger, {
        "cmd": "pair_approve", "pairing_code": code, "delegation_cert": cert})
    err = _last(ws_stranger, "error")
    assert err is not None and err["message"] == "pairing_not_found"
    assert code in gateway._pairing_sessions  # untouched


@pytest.mark.asyncio
async def test_expired_session_pruned(gateway, noauth_env):
    account = Ed25519PrivateKey.generate()
    ws_primary = _mock_ws()
    await _register(gateway, ws_primary, _did(account))
    await gateway.process_command(ws_primary, {"cmd": "pair_start"})
    code = _last(ws_primary, "pairing_started")["pairing_code"]
    # Force expiry.
    gateway._pairing_sessions[code]["expires_at"] = 0
    ws_device = _mock_ws()
    await gateway.process_command(ws_device, {
        "cmd": "pair_submit", "pairing_code": code, "device_did": _did(Ed25519PrivateKey.generate())})
    assert _last(ws_device, "pairing_invalid") is not None
    assert code not in gateway._pairing_sessions


@pytest.mark.asyncio
async def test_unregistered_new_device_can_cancel(gateway, noauth_env):
    """The new device is not yet registered when it backs out — pair_cancel must
    still work (pre-auth exempt) and notify the waiting primary."""
    account = Ed25519PrivateKey.generate()
    ws_primary = _mock_ws()
    await _register(gateway, ws_primary, _did(account))
    await gateway.process_command(ws_primary, {"cmd": "pair_start"})
    code = _last(ws_primary, "pairing_started")["pairing_code"]

    ws_device = _mock_ws()  # never registered
    await gateway.process_command(ws_device, {
        "cmd": "pair_submit", "pairing_code": code, "device_did": _did(Ed25519PrivateKey.generate())})
    ws_primary.send.reset_mock()
    await gateway.process_command(ws_device, {"cmd": "pair_cancel", "pairing_code": code})

    # Not rejected as "Not registered"; session gone; primary told it was cancelled.
    assert _last(ws_device, "error") is None or _last(ws_device, "error").get("message") != "Not registered"
    assert code not in gateway._pairing_sessions
    assert _last(ws_primary, "pairing_cancelled") is not None
