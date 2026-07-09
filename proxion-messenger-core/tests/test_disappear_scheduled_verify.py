"""Verification: disappearing messages actually purge the STORE (normal rooms),
and scheduled messages fire cross-gateway to a federated DM."""
from __future__ import annotations

import json
import asyncio
from datetime import datetime, timezone, timedelta

import pytest
from unittest.mock import AsyncMock, patch
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


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


def _one_shot_sleep():
    """asyncio.sleep replacement that lets the loop run its body exactly once."""
    count = {"n": 0}

    async def fake_sleep(_):
        count["n"] += 1
        if count["n"] >= 2:
            raise asyncio.CancelledError()
    return fake_sleep


@pytest.mark.asyncio
async def test_room_disappear_purges_store_not_just_memory(tmp_path, noauth_env):
    """A NORMAL room (history_mode != 'all', so no in-memory message buffer) must
    still have its disappearing messages deleted from the STORE — the bug was that
    the store purge was gated on the empty in-memory list."""
    gw = _gw(tmp_path, "a", 9301)
    ws = _mock_ws(); await _register(gw, ws, _did(Ed25519PrivateKey.generate()))
    room_id = "room-store-purge"
    gw._local_rooms[room_id] = {"name": "r", "members": {ws}, "messages": [], "history_mode": "none"}
    gw._room_disappear_timers[room_id] = 500  # 500 ms

    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    new_ts = datetime.now(timezone.utc).isoformat()
    # Messages live only in the STORE (normal room), not room["messages"].
    gw._store.save_message("old-1", room_id, "room", "did:key:zS", "S", "old", old_ts)
    gw._store.save_message("new-1", room_id, "room", "did:key:zS", "S", "new", new_ts)

    with patch("asyncio.sleep", side_effect=_one_shot_sleep()):
        try:
            await gw._expire_messages_loop()
        except asyncio.CancelledError:
            pass

    assert gw._store.get_message("old-1") is None, "expired message must be purged from the store"
    assert gw._store.get_message("new-1") is not None, "recent message must survive"
    # Members told to purge by cutoff (store-only room path).
    assert any(e["thread_id"] == room_id for e in _events(ws, "dm_messages_expired"))


@pytest.mark.asyncio
async def test_scheduled_message_fires_to_federated_dm(tmp_path, noauth_env, monkeypatch):
    gw_a = _gw(tmp_path, "a", 9302)
    gw_b = _gw(tmp_path, "b", 9303)
    a_url = gw_a._gateway_http_url()
    ga_did = pub_key_to_did(gw_a.agent.identity_pub_bytes)
    gb_did = pub_key_to_did(gw_b.agent.identity_pub_bytes)
    alice = _did(Ed25519PrivateKey.generate())
    bob = _did(Ed25519PrivateKey.generate())

    async def _route(url, payload):
        target = gw_a if a_url.rstrip("/") in url else gw_b
        status, _ = await target._handle_relay_post(json.dumps(payload).encode())
        return status.startswith("2")
    monkeypatch.setattr("proxion_messenger_core.relay.post_relay", _route)

    # Alice ↔ Bob federated DM (cert-A on Alice's side, peer = Bob's gateway did).
    gw_a._store.save_relationship(
        {"certificate_id": "cert-A", "subject": "ab" * 32, "created_at": 0,
         "expires_at": 2**31 - 1}, peer_did=gb_did, owner_webid=alice)
    gw_a._store.save_dm_thread("cert-A", gb_did, None, owner_webid=alice)
    gw_a._peer_gateway_urls[gb_did] = gw_b._gateway_http_url()
    gw_b._store.save_relationship(
        {"certificate_id": "cert-B", "subject": "cd" * 32, "created_at": 0,
         "expires_at": 2**31 - 1}, peer_did=ga_did, owner_webid=bob)

    ws_a = _mock_ws(); await _register(gw_a, ws_a, alice)
    ws_b = _mock_ws(); await _register(gw_b, ws_b, bob)
    ws_b.send.reset_mock()

    # Schedule a DM into the federated thread, already due.
    gw_a._store.save_scheduled_message({
        "id": "sch-1", "thread_id": "cert-A", "from_webid": alice,
        "content": "scheduled hello", "send_at": __import__("time").time() - 1,
        "created_at": __import__("time").time() - 10,
    })

    with patch("asyncio.sleep", side_effect=_one_shot_sleep()):
        try:
            await gw_a._scheduler_loop()
        except asyncio.CancelledError:
            pass
    await asyncio.sleep(0.05)

    msgs = [e for e in _events(ws_b, "message") if "scheduled hello" in json.dumps(e)]
    assert msgs, "Bob must receive Alice's scheduled cross-gateway DM"
