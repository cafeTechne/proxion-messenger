"""End-to-end cross-gateway CHUNKED FILE TRANSFER (R39) verification.

Two gateways, two friends. Drives the real client command flow:
  file_offer -> file_accept -> file_chunk(s) -> file_complete
and asserts every signal actually reaches the peer on the other gateway and the
chunk bytes arrive intact. Exercises the same cross-gateway identity/routing path
that silently broke DMs, reactions, and group voice.
"""
from __future__ import annotations

import base64
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


def _gw(tmp_path, name, port):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", http_public_url=f"http://127.0.0.1:{port}",
                             db_path=str(tmp_path / f"{name}.db")),
    )


def _seed_rel(gw, cert_id, peer_did, owner=""):
    gw._store.save_relationship(
        {"certificate_id": cert_id, "subject": "ab" * 32, "created_at": 0,
         "expires_at": 2**31 - 1}, peer_did=peer_did, owner_webid=owner)


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


@pytest.mark.asyncio
async def test_cross_gateway_chunked_file_transfer(tmp_path, noauth_env, monkeypatch):
    gw_a = _gw(tmp_path, "a", 9201)
    gw_b = _gw(tmp_path, "b", 9202)
    a_url = gw_a._gateway_http_url()
    b_url = gw_b._gateway_http_url()
    ga_did = pub_key_to_did(gw_a.agent.identity_pub_bytes)
    gb_did = pub_key_to_did(gw_b.agent.identity_pub_bytes)

    alice = _did(Ed25519PrivateKey.generate())
    bob = _did(Ed25519PrivateKey.generate())

    async def _route(url, payload):
        target = gw_a if a_url.rstrip("/") in url else gw_b
        status, _ = await target._handle_relay_post(json.dumps(payload).encode())
        return status.startswith("2")
    monkeypatch.setattr("proxion_messenger_core.relay.post_relay", _route)

    # Alice and Bob are friends. As in the real UI (view.js sets peerWebid =
    # contact.peer_did), a federated peer is addressed by their GATEWAY did — so
    # to_webid is gb_did / ga_did, and each side keys the relationship + peer
    # gateway route by the other's gateway did.
    _seed_rel(gw_a, "cert-A", gb_did, owner=alice)
    gw_a._peer_gateway_urls[gb_did] = b_url          # GA → Bob's gateway
    _seed_rel(gw_b, "cert-B", ga_did, owner=bob)
    gw_b._peer_gateway_urls[ga_did] = a_url          # GB → Alice's gateway

    ws_a = _mock_ws(); await _register(gw_a, ws_a, alice)
    ws_b = _mock_ws(); await _register(gw_b, ws_b, bob)
    ws_b.send.reset_mock()

    file_bytes = b"proxion file transfer payload " * 40   # ~1.2 KB
    data_b64 = base64.b64encode(file_bytes).decode()

    # 1) offer → 2) accept → 3) chunk → 4) complete, each driven as a client cmd.
    # Alice addresses Bob's gateway did (his DM peer identity); the receiver replies
    # to the offer's from_webid (Alice's gateway did), exactly like the web client.
    await gw_a.process_command(ws_a, {
        "cmd": "file_offer", "to_webid": gb_did, "file_id": "f1",
        "filename": "hello.txt", "mime_type": "text/plain",
        "size_bytes": len(file_bytes), "total_chunks": 1,
    })
    await asyncio.sleep(0.03)
    offers = _events(ws_b, "file_offer")
    assert offers, "Bob must receive Alice's file_offer"
    assert offers[0]["from_webid"] == ga_did          # attributed to Alice's gateway
    _reply_to = offers[0]["from_webid"]

    ws_a.send.reset_mock()
    await gw_b.process_command(ws_b, {"cmd": "file_accept", "to_webid": _reply_to, "file_id": "f1"})
    await asyncio.sleep(0.03)
    assert _events(ws_a, "file_accept"), "Alice must receive Bob's file_accept"

    ws_b.send.reset_mock()
    await gw_a.process_command(ws_a, {
        "cmd": "file_chunk", "to_webid": gb_did, "file_id": "f1", "seq": 0, "data": data_b64,
    })
    await asyncio.sleep(0.03)
    chunks = _events(ws_b, "file_chunk")
    assert chunks, "Bob must receive the file chunk"
    assert chunks[0]["data"] == data_b64, "chunk bytes must arrive intact"

    await gw_a.process_command(ws_a, {
        "cmd": "file_complete", "to_webid": gb_did, "file_id": "f1",
    })
    await asyncio.sleep(0.03)
    assert _events(ws_b, "file_complete"), "Bob must receive file_complete"
