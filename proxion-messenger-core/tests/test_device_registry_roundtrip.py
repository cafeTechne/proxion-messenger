"""End-to-end device-registry round-trip through the gateway command layer.

Covers the register_device -> list_devices -> revoke_device_and_rekey flow that
underpins multi-device linking (each linked device shares the account identity
and is tracked here for visibility/revocation). This is distinct from
test_gateway_multi_device.py, which covers live *session* multiplexing under one
DID; here we exercise the persistent *device registry* + Ed25519 attestation.
"""
from __future__ import annotations

import base64
import json
import time

import pytest
from unittest.mock import AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.device_registry import (
    generate_device_key,
    sign_device_attestation,
)

OWNER = "https://alice.pod/profile/card#me"


@pytest.fixture
def agent():
    a = AgentState.generate()
    a.webid = OWNER
    return a


@pytest.fixture
def gateway(agent, tmp_path):
    # A real store is required — the device registry persists to SQLite.
    return ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(db_path=str(tmp_path / "devreg.db")),
    )


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


async def _register(gw, ws, webid=OWNER):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": webid, "display_name": "Alice"})


def _attested_device(owner=OWNER):
    """Return (register_payload, device_id) with a valid self-attestation."""
    key = generate_device_key()
    ts = time.time()
    priv = base64.b64decode(key["priv_b64"])
    sig = sign_device_attestation(priv, owner, key["device_id"], ts)
    return {
        "cmd": "register_device",
        "device_id": key["device_id"],
        "device_pub_b64": key["pub_b64"],
        "attestation_b64": sig,
        "timestamp": ts,
    }, key["device_id"]


def _last_of_type(ws, type_):
    for call in reversed(ws.send.call_args_list):
        msg = json.loads(call[0][0])
        if msg.get("type") == type_:
            return msg
    return None


@pytest.mark.asyncio
async def test_register_device_persists_and_lists(gateway):
    ws = _mock_ws()
    await _register(gateway, ws)
    payload, device_id = _attested_device()

    ws.send.reset_mock()
    await gateway.process_command(ws, payload)
    ok = _last_of_type(ws, "device_registered")
    assert ok is not None and ok["device_id"] == device_id
    assert ok["owner_webid"] == OWNER

    ws.send.reset_mock()
    await gateway.process_command(ws, {"cmd": "list_devices"})
    listing = _last_of_type(ws, "devices")
    assert listing is not None
    ids = [d["device_id"] for d in listing["devices"]]
    assert device_id in ids
    # attestation bytes must never be echoed back to clients
    assert all("attestation_b64" not in d for d in listing["devices"])


@pytest.mark.asyncio
async def test_register_device_rejects_forged_attestation(gateway):
    ws = _mock_ws()
    await _register(gateway, ws)
    payload, device_id = _attested_device()
    # Corrupt the attestation: valid base64, wrong signature.
    payload["attestation_b64"] = base64.b64encode(b"\x00" * 64).decode()

    ws.send.reset_mock()
    await gateway.process_command(ws, payload)
    err = _last_of_type(ws, "error")
    assert err is not None and err["message"] == "invalid_attestation"
    # And nothing was persisted.
    assert gateway._store.get_device(device_id) is None


@pytest.mark.asyncio
async def test_register_device_rejects_attestation_bound_to_other_owner(gateway):
    """An attestation signed for a different owner_webid must not register."""
    ws = _mock_ws()
    await _register(gateway, ws)
    # Sign the attestation against a DIFFERENT owner than the connection's.
    payload, device_id = _attested_device(owner="https://mallory.pod/card#me")

    ws.send.reset_mock()
    await gateway.process_command(ws, payload)
    err = _last_of_type(ws, "error")
    assert err is not None and err["message"] == "invalid_attestation"
    assert gateway._store.get_device(device_id) is None


@pytest.mark.asyncio
async def test_revoke_device_removes_and_notifies_other_sessions(gateway):
    # Two live sessions for the same account; register a device from session 1.
    ws1 = _mock_ws()
    ws2 = _mock_ws()
    await _register(gateway, ws1)
    await _register(gateway, ws2)
    payload, device_id = _attested_device()
    await gateway.process_command(ws1, payload)
    assert gateway._store.get_device(device_id) is not None

    ws1.send.reset_mock()
    ws2.send.reset_mock()
    await gateway.process_command(ws1, {"cmd": "revoke_device_and_rekey", "device_id": device_id})

    # Caller gets an ack; the OTHER session is told to rekey.
    assert _last_of_type(ws1, "device_revoked_ack") is not None
    notice = _last_of_type(ws2, "device_revoked")
    assert notice is not None and notice["device_id"] == device_id
    # Registry no longer holds it.
    assert gateway._store.get_device(device_id) is None


@pytest.mark.asyncio
async def test_revoke_device_rejects_foreign_owner(gateway):
    """A session cannot revoke a device it does not own."""
    ws_alice = _mock_ws()
    await _register(gateway, ws_alice)
    payload, device_id = _attested_device()
    await gateway.process_command(ws_alice, payload)

    # Bob registers on a separate connection and tries to revoke Alice's device.
    ws_bob = _mock_ws()
    await _register(gateway, ws_bob, webid="https://bob.pod/profile/card#me")
    ws_bob.send.reset_mock()
    await gateway.process_command(ws_bob, {"cmd": "revoke_device_and_rekey", "device_id": device_id})

    err = _last_of_type(ws_bob, "error")
    assert err is not None and err["message"] == "device_not_found"
    # Alice's device is untouched.
    assert gateway._store.get_device(device_id) is not None
