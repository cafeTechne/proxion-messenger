"""Cross-gateway DM delete-for-everyone (R54).

delete_local_message's DM branch delivered only to LOCAL sessions
(_send_to_identity(peer)), so a federated peer never saw the deletion. Now it
relays dm_delete to the peer's gateway, which (only if the peer authored the
message) removes its stored copy and emits message_deleted keyed to ITS cert id.
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
async def test_cross_gateway_dm_delete_relays(tmp_path, noauth_env, monkeypatch):
    gw_a = _gw(tmp_path, "a")
    gw_b = _gw(tmp_path, "b")
    b_did = _did(Ed25519PrivateKey.generate())
    a_did = _did(Ed25519PrivateKey.generate())
    _seed_rel(gw_a, "cert-A", b_did, owner=a_did)
    gw_a._peer_gateway_urls[b_did] = "http://gw-b.test"
    _seed_rel(gw_b, "cert-B", a_did, owner=b_did)
    # A's DM thread with B (keyed by A's cert id) + a message A authored.
    gw_a._store.save_dm_thread("cert-A", b_did, None, owner_webid=a_did)
    gw_a._store.save_message("m-1", "cert-A", "dm", a_did, "A", "hi", "2026-01-01T00:00:00Z")
    # B has the same message stored (received), authored by A.
    gw_b._store.save_message("m-1", "cert-B", "dm", a_did, "A", "hi", "2026-01-01T00:00:00Z")

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
        "cmd": "delete_local_message", "message_id": "m-1", "thread_id": "cert-A",
    })
    await asyncio.sleep(0.1)

    ev = _events(ws_b, "message_deleted")
    assert ev, "the cross-gateway peer must receive the delete"
    assert ev[0]["message_id"] == "m-1"
    assert ev[0]["thread_id"] == "cert-B"
    assert gw_b._store.get_message("m-1") is None, "peer's stored copy must be removed"


@pytest.mark.asyncio
async def test_dm_delete_relay_rejects_deleting_others_message(tmp_path):
    """A peer can only delete a message THEY authored from our store/view."""
    gw = _gw(tmp_path, "c")
    recipient = _did(Ed25519PrivateKey.generate())
    attacker = _did(Ed25519PrivateKey.generate())
    _seed_rel(gw, "cert-C", attacker, owner=recipient)
    # A message authored by the RECIPIENT, not the attacker.
    gw._store.save_message("m-victim", "cert-C", "dm", recipient, "R", "mine", "2026-01-01T00:00:00Z")
    ws = _mock_ws()
    await _register(gw, ws, recipient)
    ws.send.reset_mock()
    status, _ = await gw._handle_relay_post(json.dumps({
        "content_type": "dm_delete", "from_webid": attacker,
        "to_webid": recipient, "message_id": "m-victim",
    }).encode())
    assert status.startswith("200")
    assert gw._store.get_message("m-victim") is not None, "must NOT delete the recipient's own message"
    assert not _events(ws, "message_deleted")
