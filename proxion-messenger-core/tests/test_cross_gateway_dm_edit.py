"""Cross-gateway DM edits (R54).

edit_message only handled cert_id in dm_clients (pod path); a relay-only
federated DM edit returned "Unknown DM recipient" — editing a cross-gateway DM
did nothing. Now it updates the store, echoes the editor, and relays a dm_edit
to the peer's gateway, which maps from_webid -> ITS cert_id and delivers
message_edited.
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
async def test_cross_gateway_dm_edit_relays_and_maps_cert(tmp_path, noauth_env, monkeypatch):
    gw_a = _gw(tmp_path, "a")
    gw_b = _gw(tmp_path, "b")
    b_did = _did(Ed25519PrivateKey.generate())
    a_priv = Ed25519PrivateKey.generate()
    a_did = _did(a_priv)
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
    ws_a.send.reset_mock(); ws_b.send.reset_mock()

    await gw_a.process_command(ws_a, {
        "cmd": "edit_message", "cert_id": "cert-A",
        "message_id": "m-1", "content": "edited text",
    })
    await asyncio.sleep(0.1)

    # Editor sees their own edit; no "Unknown DM recipient" error.
    assert _events(ws_a, "message_edited"), "editor must get the echo"
    assert not any(e.get("message") == "Unknown DM recipient: cert-A" for e in _events(ws_a, "error"))
    # Peer receives the edit keyed to ITS cert id.
    ev = _events(ws_b, "message_edited")
    assert ev, "the cross-gateway peer must receive the edit"
    assert ev[0]["new_content"] == "edited text"
    assert ev[0]["thread_id"] == "cert-B"


@pytest.mark.asyncio
async def test_cross_gateway_dm_edit_accepts_local_caller(tmp_path, noauth_env):
    """Cert DMs are gateway-scoped (federation identity = gateway did, not the
    browser session did), so a locally-registered caller is the gateway's user
    and authorized — no 'Not a participant' rejection based on the did split."""
    gw = _gw(tmp_path, "c")
    _seed_rel(gw, "cert-C", _did(Ed25519PrivateKey.generate()), owner="")
    ws = _mock_ws()
    await _register(gw, ws, _did(Ed25519PrivateKey.generate()))
    ws.send.reset_mock()
    await gw.process_command(ws, {
        "cmd": "edit_message", "cert_id": "cert-C", "message_id": "m", "content": "x",
    })
    assert not any("participant" in (e.get("message") or "").lower() for e in _events(ws, "error"))
    assert _events(ws, "message_edited")
