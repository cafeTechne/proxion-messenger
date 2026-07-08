"""DM message pinning (R54).

Pinning a DM message used to ALWAYS error "Only the room owner can pin"
(_check_room_permission returns False for a non-room) and was never persisted
or sent to the peer. A DM has no owner — either participant may pin. Now pins
persist (keyed by thread_id), reload via get_pins, and deliver to the peer
(local or cross-gateway relay).
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
async def test_dm_pin_persists_and_reloads(tmp_path, noauth_env):
    gw = _gw(tmp_path, "a")
    a_did = _did(Ed25519PrivateKey.generate())
    _seed_rel(gw, "cert-A", "did:key:zPeer", owner=a_did)
    gw._store.save_message("m-1", "cert-A", "dm", a_did, "A", "important", "2026-01-01T00:00:00Z")
    ws = _mock_ws()
    await _register(gw, ws, a_did)
    ws.send.reset_mock()
    # Pin — no "owner" error, message_pinned echoed.
    await gw.process_command(ws, {"cmd": "pin_message", "message_id": "m-1", "thread_id": "cert-A"})
    assert not any("owner can pin" in (e.get("message") or "") for e in _events(ws, "error"))
    assert _events(ws, "message_pinned"), "the pinner must get message_pinned"
    # get_pins reads it back (was always [] for DMs).
    ws.send.reset_mock()
    await gw.process_command(ws, {"cmd": "get_pins", "thread_id": "cert-A"})
    pins = _events(ws, "pins")[0]["pins"]
    assert any(p["message_id"] == "m-1" for p in pins)
    # Unpin removes it.
    await gw.process_command(ws, {"cmd": "unpin_message", "message_id": "m-1", "thread_id": "cert-A"})
    ws.send.reset_mock()
    await gw.process_command(ws, {"cmd": "get_pins", "thread_id": "cert-A"})
    assert not _events(ws, "pins")[0]["pins"]


@pytest.mark.asyncio
async def test_dm_pin_relays_cross_gateway(tmp_path, noauth_env, monkeypatch):
    gw_a = _gw(tmp_path, "a2")
    gw_b = _gw(tmp_path, "b2")
    b_did = _did(Ed25519PrivateKey.generate())
    a_did = _did(Ed25519PrivateKey.generate())
    _seed_rel(gw_a, "cert-A", b_did, owner=a_did)
    gw_a._peer_gateway_urls[b_did] = "http://gw-b.test"
    _seed_rel(gw_b, "cert-B", pub_key_to_did(gw_a.agent.identity_pub_bytes), owner=b_did)
    async def _fake_post(url, payload):
        status, _ = await gw_b._handle_relay_post(json.dumps(payload).encode())
        return status.startswith("200")
    monkeypatch.setattr("proxion_messenger_core.relay.post_relay", _fake_post)
    ws_b = _mock_ws()
    await _register(gw_b, ws_b, b_did)
    ws_a = _mock_ws()
    await _register(gw_a, ws_a, a_did)
    ws_b.send.reset_mock()
    await gw_a.process_command(ws_a, {"cmd": "pin_message", "message_id": "m-9", "thread_id": "cert-A"})
    await asyncio.sleep(0.1)
    ev = _events(ws_b, "message_pinned")
    assert ev and ev[0]["message_id"] == "m-9"
    assert ev[0]["thread_id"] == "cert-B", "pin must attach to the peer's cert id"
    # And it's persisted on B's side.
    assert any(p["message_id"] == "m-9" for p in gw_b._store.get_pins("cert-B"))


@pytest.mark.asyncio
async def test_dm_pin_accepts_local_caller(tmp_path, noauth_env):
    """Cert DMs are gateway-scoped: federation identity is the gateway did, not the
    browser session did, so a locally-registered caller is the gateway's user and
    authorized. There is no owner==caller check to reject on for a cert DM."""
    gw = _gw(tmp_path, "c")
    _seed_rel(gw, "cert-C", _did(Ed25519PrivateKey.generate()), owner="")
    gw._store.save_message("m", "cert-C", "dm", "did:key:zSomeone", "S", "hi", "2026-01-01T00:00:00Z")
    ws = _mock_ws()
    await _register(gw, ws, _did(Ed25519PrivateKey.generate()))
    ws.send.reset_mock()
    await gw.process_command(ws, {"cmd": "pin_message", "message_id": "m", "thread_id": "cert-C"})
    assert not any("participant" in (e.get("message") or "").lower() for e in _events(ws, "error"))
    assert _events(ws, "message_pinned")
