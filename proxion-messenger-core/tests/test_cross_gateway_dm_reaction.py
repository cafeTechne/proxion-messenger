"""Cross-gateway DM reactions (R54).

A reaction to a relay-only federated DM was silently lost: it hit the local-DM
branch which does _send_to_identity(cert_id) — but cert_id is a UUID there, so
it matched no socket, and there was no relay. There was no dm_reaction relay at
all (only room_reaction). Now: the reaction relays to the peer's gateway, which
maps from_webid -> ITS OWN cert_id (the cert_id asymmetry) and delivers
reaction_added/removed to the local recipient.
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
async def test_dm_reaction_relays_cross_gateway_and_maps_cert_id(tmp_path, noauth_env, monkeypatch):
    gw_a = _gw(tmp_path, "a")   # reactor's gateway
    gw_b = _gw(tmp_path, "b")   # recipient's gateway
    b_priv = Ed25519PrivateKey.generate()
    b_did = _did(b_priv)
    a_priv = Ed25519PrivateKey.generate()
    a_did = _did(a_priv)

    # A holds the relationship with B under cert "cert-A"; A knows B's gateway.
    _seed_rel(gw_a, "cert-A", b_did, owner=a_did)
    gw_a._peer_gateway_urls[b_did] = "http://gw-b.test"
    # B holds the SAME relationship under a DIFFERENT cert id (cert_id asymmetry).
    _seed_rel(gw_b, "cert-B-side", pub_key_to_did(gw_a.agent.identity_pub_bytes), owner=b_did)

    # Route A's ephemeral relay into B's /relay handler in-process.
    async def _fake_post(url, payload):
        status, _ = await gw_b._handle_relay_post(json.dumps(payload).encode())
        return status.startswith("200")
    monkeypatch.setattr("proxion_messenger_core.relay.post_relay", _fake_post)

    # B is connected on gw_b.
    ws_b = _mock_ws()
    await _register(gw_b, ws_b, b_did)

    # A's user reacts to a message in the A<->B DM (thread = A's cert id).
    ws_a = _mock_ws()
    await _register(gw_a, ws_a, a_did)
    ws_b.send.reset_mock()
    await gw_a.process_command(ws_a, {
        "cmd": "add_reaction", "cert_id": "cert-A",
        "message_id": "m-42", "emoji": "🔥",
    })
    await asyncio.sleep(0.1)  # let the fire-and-forget ephemeral relay task run

    # B receives reaction_added, keyed to B's OWN cert id, not A's.
    evs = _events(ws_b, "reaction_added")
    assert evs, "the cross-gateway peer must receive the reaction"
    assert evs[0]["message_id"] == "m-42"
    assert evs[0]["emoji"] == "🔥"
    assert evs[0]["thread_id"] == "cert-B-side", "reaction must attach to B's cert id"
    assert evs[0]["from_webid"] == pub_key_to_did(gw_a.agent.identity_pub_bytes)


@pytest.mark.asyncio
async def test_dm_reaction_removal_relays(tmp_path, noauth_env, monkeypatch):
    gw_a = _gw(tmp_path, "a2")
    gw_b = _gw(tmp_path, "b2")
    b_did = _did(Ed25519PrivateKey.generate())
    a_did = _did(Ed25519PrivateKey.generate())
    _seed_rel(gw_a, "cert-A2", b_did, owner=a_did)
    gw_a._peer_gateway_urls[b_did] = "http://gw-b2.test"
    _seed_rel(gw_b, "cert-B2", pub_key_to_did(gw_a.agent.identity_pub_bytes), owner=b_did)
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
        "cmd": "remove_reaction", "cert_id": "cert-A2",
        "message_id": "m-9", "emoji": "👍",
    })
    await asyncio.sleep(0.1)
    evs = _events(ws_b, "reaction_removed")
    assert evs and evs[0]["message_id"] == "m-9" and evs[0]["thread_id"] == "cert-B2"


@pytest.mark.asyncio
async def test_dm_reaction_relay_from_stranger_dropped(tmp_path):
    """A gateway with no relationship to the from_webid can't inject reactions."""
    gw = _gw(tmp_path, "b3")
    ws = _mock_ws()
    await _register(gw, ws, "did:key:zRecipient")
    ws.send.reset_mock()
    status, _ = await gw._handle_relay_post(json.dumps({
        "content_type": "dm_reaction",
        "from_webid": "did:key:zStranger", "to_webid": "did:key:zRecipient",
        "message_id": "m-x", "emoji": "😈", "action": "add",
    }).encode())
    assert status.startswith("200")            # no block-reveal
    assert not _events(ws, "reaction_added")   # but nothing delivered
