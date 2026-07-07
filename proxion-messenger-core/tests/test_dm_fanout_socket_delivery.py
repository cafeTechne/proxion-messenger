"""Gateway delivers per-device DM fanout envelopes to a multi-device account (slice 5)."""
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


def _events(ws, type_):
    out = []
    for call in ws.send.call_args_list:
        msg = json.loads(call[0][0])
        if msg.get("type") == type_:
            out.append(msg)
    return out


@pytest.fixture
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "fanout.db")),
    )


async def _register(gw, ws, did, cert=None):
    gw.clients.add(ws)
    p = {"cmd": "register", "did": did, "display_name": "D"}
    if cert is not None:
        p["delegation_cert"] = cert
    await gw.process_command(ws, p)


@pytest.mark.asyncio
async def test_fanout_reaches_all_devices_of_the_account(gateway, noauth_env):
    # Account A with two devices (primary + delegated), and a peer B sender.
    account = Ed25519PrivateKey.generate()
    device = Ed25519PrivateKey.generate()
    account_did, device_did = _did(account), _did(device)
    cert = issue_device_cert(account, device_did)

    ws_a1 = _mock_ws()   # primary  (device_id == account_did)
    ws_a2 = _mock_ws()   # delegated (device_id == device_did)
    ws_b = _mock_ws()    # peer/sender
    await _register(gateway, ws_a1, account_did)
    await _register(gateway, ws_a2, device_did, cert=cert)
    await _register(gateway, ws_b, _did(Ed25519PrivateKey.generate()))

    ws_a1.send.reset_mock()
    ws_a2.send.reset_mock()
    await gateway.process_command(ws_b, {
        "cmd": "send_dm_fanout",
        "message_id": "m-1",
        "from_webid": gateway._client_webids[ws_b],
        "fanout": [
            {"to_webid": account_did, "to_device_id": account_did, "payload": {"content": "for-a1"}},
            {"to_webid": account_did, "to_device_id": device_did, "payload": {"content": "for-a2"}},
        ],
    })

    # Delivery is DEVICE-addressed: each device receives exactly its own
    # envelope. (Was per-account — every socket got every envelope and filtered
    # client-side — which hid offline devices from the queue/push logic.)
    evs_a1 = _events(ws_a1, "dm_fanout")
    evs_a2 = _events(ws_a2, "dm_fanout")
    assert {e["to_device_id"] for e in evs_a1} == {account_did}, "primary must get ONLY its envelope"
    assert {e["to_device_id"] for e in evs_a2} == {device_did}, "delegated device must get ONLY its envelope"
    assert all(e["message_id"] == "m-1" for e in evs_a1 + evs_a2)

    # Sender gets an ack listing both deliveries.
    ack = _events(ws_b, "send_dm_fanout_ack")
    assert ack and len(ack[-1]["delivered"]) == 2


@pytest.mark.asyncio
async def test_fanout_requires_message_id_and_entries(gateway, noauth_env):
    ws = _mock_ws()
    await _register(gateway, ws, _did(Ed25519PrivateKey.generate()))
    ws.send.reset_mock()
    await gateway.process_command(ws, {"cmd": "send_dm_fanout", "message_id": "", "fanout": []})
    errs = _events(ws, "error")
    assert errs and "fanout" in errs[-1]["message"]


@pytest.mark.asyncio
async def test_fanout_from_webid_cannot_be_spoofed(gateway, noauth_env):
    """A client-supplied from_webid must be ignored — sender identity comes from
    the authenticated session, otherwise fanout DMs allow impersonation."""
    target_did = _did(Ed25519PrivateKey.generate())
    mallory_did = _did(Ed25519PrivateKey.generate())
    ws_target = _mock_ws()
    ws_mallory = _mock_ws()
    await _register(gateway, ws_target, target_did)
    await _register(gateway, ws_mallory, mallory_did)

    ws_target.send.reset_mock()
    await gateway.process_command(ws_mallory, {
        "cmd": "send_dm_fanout", "message_id": "m-spoof",
        "from_webid": "did:key:zSomeoneElse",   # forged
        "fanout": [{"to_webid": target_did, "to_device_id": target_did, "payload": {"content": "hi"}}],
    })
    evs = _events(ws_target, "dm_fanout")
    assert evs, "envelope should still deliver"
    assert all(e["from_webid"] == mallory_did for e in evs), "forged from_webid must be overwritten"


@pytest.mark.asyncio
async def test_fanout_entry_count_is_capped(gateway, noauth_env):
    ws = _mock_ws()
    await _register(gateway, ws, _did(Ed25519PrivateKey.generate()))
    ws.send.reset_mock()
    big = [{"to_webid": f"did:key:z{i}", "to_device_id": f"d{i}", "payload": {}} for i in range(64)]
    await gateway.process_command(ws, {"cmd": "send_dm_fanout", "message_id": "m-big", "fanout": big})
    errs = _events(ws, "error")
    assert errs and "too large" in errs[-1]["message"]
