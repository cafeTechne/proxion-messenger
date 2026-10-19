"""Events addressed to one local account must not spill onto every session.

_sockets_for() falls back to ALL connected sockets when asked for this gateway's
own DID and nobody is registered under it. That is right for a DM addressed to
the gateway's Proxion address, but room_updated, relay_expired, the inbound
typing relay and dm_messages_expired used it (or a raw loop over every socket)
for deliveries meant for a specific member or account.
"""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest

from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState


def _gw(tmp_path):
    return ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                          config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "s.db")))


def _ws():
    w = AsyncMock()
    w.__hash__ = lambda s: id(s)
    w.__eq__ = lambda s, o: s is o
    return w


def _attach(gw, ws, did):
    gw.clients.add(ws)
    gw._client_webids[ws] = did
    gw._webid_sockets.setdefault(did, set()).add(ws)


def _types(ws):
    return [json.loads(c.args[0]).get("type") for c in ws.send.call_args_list]


@pytest.mark.asyncio
async def test_room_update_does_not_reach_a_non_member(tmp_path):
    gw = _gw(tmp_path)
    owner_did = gw._own_gateway_did()          # stored member, offline
    admin, outsider = _ws(), _ws()
    _attach(gw, admin, "did:key:z6MkAdmin")
    _attach(gw, outsider, "did:key:z6MkOutsider")
    rid = "r"
    gw._local_rooms[rid] = {"name": "Private", "code": "x", "members": {admin}, "invite_url": "",
                            "history_mode": "none", "messages": [], "creator_webid": owner_did}
    gw._store.add_room_member(rid, owner_did)
    gw._store.add_room_member(rid, "did:key:z6MkAdmin")
    gw._store.set_room_role(rid, "did:key:z6MkAdmin", "admin")
    await gw._handle_update_room(admin, {"room_id": rid, "name": "Layoffs", "topic": "list"})
    assert "room_updated" in _types(admin)
    assert "room_updated" not in _types(outsider)


@pytest.mark.asyncio
async def test_relay_expired_only_reaches_the_sender(tmp_path):
    gw = _gw(tmp_path)
    own = gw._own_gateway_did()
    other = _ws()
    _attach(gw, other, "did:key:z6MkOther")
    gw._store.enqueue_relay("relay-1", "did:key:bob", "wss://bob.example.com",
                            {"from_webid": own, "to_webid": "did:key:bob", "_q_sender": own})
    with gw._store._conn() as conn:
        conn.execute("UPDATE pending_relays SET expires_at = ? WHERE id = ?",
                     (time.time() - 10, "relay-1"))
    await gw._expire_stale_relays()
    assert "relay_expired" not in _types(other)


@pytest.mark.asyncio
async def test_dm_expiry_reaches_only_the_thread_owner(tmp_path):
    gw = _gw(tmp_path)
    alice, bob = _ws(), _ws()
    _attach(gw, alice, "did:key:z6MkAlice")
    _attach(gw, bob, "did:key:z6MkBob")
    gw._store.save_relationship({"certificate_id": "cert-A", "subject": "aa" * 32,
                                 "created_at": 0, "expires_at": 2**31 - 1},
                                peer_did="did:key:z6MkPeer", owner_webid="did:key:z6MkAlice")
    await gw._notify_dm_expired("cert-A", "2026-01-01T00:00:00+00:00")
    assert "dm_messages_expired" in _types(alice)
    assert "dm_messages_expired" not in _types(bob)


@pytest.mark.asyncio
async def test_typing_relay_ownerless_relationship_reaches_no_one_under_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
    gw = _gw(tmp_path)
    other = _ws()
    _attach(gw, other, "did:key:z6MkOther")
    gw._store.save_relationship({"certificate_id": "cert-L", "subject": "bb" * 32,
                                 "created_at": 0, "expires_at": 2**31 - 1},
                                peer_did="did:key:z6MkPeer", owner_webid="")
    status, _ = await gw._handle_typing_relay({"from_webid": "did:key:z6MkPeer", "cert_id": "x"})
    assert status.startswith("200")
    assert "typing" not in _types(other)


@pytest.mark.asyncio
async def test_typing_relay_ownerless_relationship_still_reaches_single_user(tmp_path, monkeypatch):
    """Auth-off single-user gateway: older rows have no owner and the local
    browser registers under its own session DID, so the gateway-DID fallback
    stays."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    gw = _gw(tmp_path)
    me = _ws()
    _attach(gw, me, "did:key:z6MkSession")
    gw._store.save_relationship({"certificate_id": "cert-L", "subject": "bb" * 32,
                                 "created_at": 0, "expires_at": 2**31 - 1},
                                peer_did="did:key:z6MkPeer", owner_webid="")
    await gw._handle_typing_relay({"from_webid": "did:key:z6MkPeer", "cert_id": "x"})
    assert "typing" in _types(me)
