"""DM disappear timers must federate (R54).

A DM disappear timer applied only on the SETTER's gateway — the peer never got
it, so the peer's copy of the "disappearing" messages persisted forever
(disappearing-messages promise broken for the recipient). Now the timer relays
to the peer's gateway, which sets it on ITS cert_id so its expiry loop deletes
the shared messages too.
"""
from __future__ import annotations

import json
import asyncio

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
    ws.send = AsyncMock(); ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _events(ws, type_):
    return [json.loads(c[0][0]) for c in ws.send.call_args_list
            if json.loads(c[0][0]).get("type") == type_]


@pytest.fixture
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


def _gw(tmp_path, name):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / f"{name}.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


def _seed_rel(gw, cert_id, peer_did, owner=""):
    gw._store.save_relationship(
        {"certificate_id": cert_id, "subject": "ab" * 32, "created_at": 0,
         "expires_at": 2**31 - 1}, peer_did=peer_did, owner_webid=owner)


@pytest.mark.asyncio
async def test_dm_disappear_timer_federates(tmp_path, noauth_env, monkeypatch):
    gw_a = _gw(tmp_path, "a")
    gw_b = _gw(tmp_path, "b")
    b_did = _did(Ed25519PrivateKey.generate())
    a_did = _did(Ed25519PrivateKey.generate())
    _seed_rel(gw_a, "cert-A", b_did, owner=a_did)
    gw_a._peer_gateway_urls[b_did] = "http://gw-b.test"
    gw_a._store.save_dm_thread("cert-A", b_did, None, owner_webid=a_did)
    _seed_rel(gw_b, "cert-B", a_did, owner=b_did)

    async def _fake_post(url, payload):
        status, _ = await gw_b._handle_relay_post(json.dumps(payload).encode())
        return status.startswith("200")
    monkeypatch.setattr("proxion_messenger_core.relay.post_relay", _fake_post)

    ws_b = _mock_ws()
    await _register(gw_b, ws_b, b_did)
    ws_a = _mock_ws()
    await _register(gw_a, ws_a, a_did)
    ws_b.send.reset_mock()

    await gw_a.process_command(ws_a, {
        "cmd": "set_disappear_timer", "room_id": "cert-A", "ms": 3_600_000,
    })
    await asyncio.sleep(0.1)

    # B's gateway now expires the shared thread too, keyed by B's cert id.
    assert gw_b._dm_disappear_timers.get("cert-B") == 3_600_000
    assert gw_b._store.get_dm_disappear_timer("cert-B") == 3_600_000
    ev = _events(ws_b, "disappear_timer_updated")
    assert ev and ev[0]["ms"] == 3_600_000 and ev[0]["room_id"] == "cert-B"


@pytest.mark.asyncio
async def test_dm_disappear_timer_survives_restart(tmp_path, noauth_env):
    """The prior UPDATE-rooms persistence was a no-op for DM ids -> DM timers
    were lost on restart. They now persist in a dedicated table and reload."""
    db = str(tmp_path / "persist.db")
    a_did = _did(Ed25519PrivateKey.generate())
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(host="127.0.0.1", db_path=db))
    _seed_rel(gw, "cert-A", "did:key:zPeer", owner=a_did)
    gw._store.save_dm_thread("cert-A", "did:key:zPeer", None, owner_webid=a_did)
    ws = _mock_ws()
    await _register(gw, ws, a_did)
    await gw.process_command(ws, {"cmd": "set_disappear_timer", "room_id": "cert-A", "ms": 600_000})
    assert gw._store.get_dm_disappear_timer("cert-A") == 600_000

    # Fresh gateway on the same DB restores the timer into memory.
    gw2 = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                         config=GatewayConfig(host="127.0.0.1", db_path=db))
    assert gw2._dm_disappear_timers.get("cert-A") == 600_000
