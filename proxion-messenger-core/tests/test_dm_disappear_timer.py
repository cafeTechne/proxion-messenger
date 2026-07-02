"""DM disappearing-message timers must actually work.

Before: _handle_set_disappear_timer only accepted rooms (room-owner check), so a
DM timer was rejected and never populated _dm_disappear_timers — the expiry loop's
DM branch was dead. Now either DM participant can set it.
"""
from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did


def _did(priv):
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
    for c in reversed(ws.send.call_args_list):
        m = json.loads(c[0][0])
        if m.get("type") == type_:
            return m
    return None


@pytest.fixture
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "disap.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


@pytest.mark.asyncio
async def test_dm_participant_can_set_disappear_timer(gateway, noauth_env):
    alice = Ed25519PrivateKey.generate()
    bob = Ed25519PrivateKey.generate()
    alice_did, bob_did = _did(alice), _did(bob)
    cert_id = "dm-cert-1"
    # A DM thread known to both participants.
    gateway._store.save_dm_thread(cert_id, bob_did, "Bob", owner_webid=alice_did)
    gateway._store.save_dm_thread(cert_id, alice_did, "Alice", owner_webid=bob_did)

    ws_alice = _mock_ws()
    ws_bob = _mock_ws()
    await _register(gateway, ws_alice, alice_did)
    await _register(gateway, ws_bob, bob_did)

    ws_alice.send.reset_mock()
    ws_bob.send.reset_mock()
    await gateway.process_command(ws_alice, {"cmd": "set_disappear_timer", "room_id": cert_id, "ms": 3600000})

    # It's wired into the expiry loop's map (previously always empty for DMs).
    assert gateway._dm_disappear_timers.get(cert_id) == 3600000
    # Both participants are notified.
    assert (_last(ws_alice, "disappear_timer_updated") or {}).get("ms") == 3600000
    assert (_last(ws_bob, "disappear_timer_updated") or {}).get("ms") == 3600000
    # And it round-trips via get.
    ws_alice.send.reset_mock()
    await gateway.process_command(ws_alice, {"cmd": "get_disappear_timer", "room_id": cert_id})
    assert (_last(ws_alice, "disappear_timer") or {}).get("ms") == 3600000


@pytest.mark.asyncio
async def test_non_participant_cannot_set_dm_timer(gateway, noauth_env):
    alice = Ed25519PrivateKey.generate()
    mallory = Ed25519PrivateKey.generate()
    cert_id = "dm-cert-2"
    gateway._store.save_dm_thread(cert_id, _did(alice), "Alice", owner_webid=_did(alice))

    ws_m = _mock_ws()
    await _register(gateway, ws_m, _did(mallory))  # not in the thread
    await gateway.process_command(ws_m, {"cmd": "set_disappear_timer", "room_id": cert_id, "ms": 1000})
    assert cert_id not in gateway._dm_disappear_timers
    assert _last(ws_m, "error") is not None
