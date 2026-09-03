"""Verification: disappearing messages actually purge the STORE (normal rooms),
and scheduled messages fire cross-gateway to a federated DM."""
from __future__ import annotations

import json
import time
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


# --------------------------------------------------------------------------
# D1: disappearing messages must be purged from the POD, not just SQLite.
# --------------------------------------------------------------------------


class _FakePod:
    """In-memory stand-in for a DPoP SolidClient: put/get/delete + one-level list."""

    def __init__(self):
        self.store: dict[str, bytes] = {}

    def put(self, uri, data, content_type=None):
        self.store[uri] = data if isinstance(data, (bytes, bytearray)) else str(data).encode()

    def get(self, uri):
        if uri not in self.store:
            raise KeyError(uri)
        return self.store[uri]

    def delete(self, uri):
        self.store.pop(uri, None)

    def list(self, prefix):
        children = set()
        for k in self.store:
            if k.startswith(prefix) and k != prefix:
                rest = k[len(prefix):]
                children.add(prefix + rest.split("/", 1)[0] + ("/" if "/" in rest else ""))
        return sorted(children)


def _attach_pod(gw):
    pod = _FakePod()
    gw._pod_webid = "https://pod.example/profile/card#me"
    gw.own_pod_clients[gw._pod_webid] = (object(), pod)
    return pod


@pytest.mark.asyncio
async def test_room_disappear_purges_pod_object(tmp_path, noauth_env):
    gw = _gw(tmp_path, "a", 9304)
    pod = _attach_pod(gw)
    ws = _mock_ws(); await _register(gw, ws, _did(Ed25519PrivateKey.generate()))
    room_id = "room-pod-purge"
    gw._local_rooms[room_id] = {"name": "r", "members": {ws}, "messages": [], "history_mode": "none"}
    gw._room_disappear_timers[room_id] = 500

    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    new_ts = datetime.now(timezone.utc).isoformat()
    gw._store.save_message("old-r", room_id, "room", "did:key:zS", "S", "old", old_ts)
    gw._store.save_message("new-r", room_id, "room", "did:key:zS", "S", "new", new_ts)
    old_uri = f"stash://pod/rooms/{room_id}/messages/old-r.json"
    new_uri = f"stash://pod/rooms/{room_id}/messages/new-r.json"
    pod.put(old_uri, b'{"message_id":"old-r"}')
    pod.put(new_uri, b'{"message_id":"new-r"}')

    with patch("asyncio.sleep", side_effect=_one_shot_sleep()):
        try:
            await gw._expire_messages_loop()
        except asyncio.CancelledError:
            pass

    assert gw._store.get_message("old-r") is None
    assert gw._store.get_message("new-r") is not None
    assert old_uri not in pod.store, "expired room message must be deleted from the pod"
    assert new_uri in pod.store, "recent room message must remain on the pod"


@pytest.mark.asyncio
async def test_dm_disappear_purges_pod_and_no_reimport(tmp_path, noauth_env):
    import hashlib
    gw = _gw(tmp_path, "a", 9305)
    pod = _attach_pod(gw)
    cert_id = "dm-pod-cert"
    gw._dm_disappear_timers = {cert_id: 500}

    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    new_ts = datetime.now(timezone.utc).isoformat()
    gw._store.save_message("old-d", cert_id, "dm", "did:key:zA", "A", "old", old_ts)
    gw._store.save_message("new-d", cert_id, "dm", "did:key:zA", "A", "new", new_ts)
    tkey = hashlib.sha256(cert_id.encode()).hexdigest()[:16]
    old_uri = f"stash://pod/local_dms/{tkey}/old-d.json"
    new_uri = f"stash://pod/local_dms/{tkey}/new-d.json"
    pod.put(old_uri, json.dumps({"message_id": "old-d", "thread_id": cert_id,
                                 "content": "old", "timestamp": old_ts}).encode())
    pod.put(new_uri, json.dumps({"message_id": "new-d", "thread_id": cert_id,
                                 "content": "new", "timestamp": new_ts}).encode())

    with patch("asyncio.sleep", side_effect=_one_shot_sleep()):
        try:
            await gw._expire_messages_loop()
        except asyncio.CancelledError:
            pass

    assert gw._store.get_message("old-d") is None
    assert gw._store.get_message("new-d") is not None
    assert old_uri not in pod.store, "expired DM message must be deleted from the pod"
    assert new_uri in pod.store

    # A cold-start restore must not resurrect the expired message.
    await gw._restore_local_dms_from_pod()
    assert gw._store.get_message("old-d") is None, "restore must not re-import an expired DM"


# --------------------------------------------------------------------------
# D2: get_message_readers must not leak readers for a thread the caller
# is not a party to (IDOR via empty/DM/federated room_id).
# --------------------------------------------------------------------------


def _connect_authed(gw, ws, webid):
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    gw._webid_sockets.setdefault(webid, set()).add(ws)


@pytest.mark.asyncio
async def test_get_message_readers_blocks_non_participant(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
    gw = _gw(tmp_path, "a", 9306)
    alice = _did(Ed25519PrivateKey.generate())
    carol = _did(Ed25519PrivateKey.generate())
    bob = _did(Ed25519PrivateKey.generate())
    cert_id = "dm-readers-cert"
    gw._store.save_message("m-read", cert_id, "dm", alice, "A", "hi", "2020-01-01T00:00:00")
    gw._store.save_dm_thread(cert_id, bob, "Bob", owner_webid=alice)
    gw._store.save_message_receipt("m-read", bob, "2020-01-01T00:05:00")

    ws_alice = _mock_ws(); _connect_authed(gw, ws_alice, alice)
    ws_carol = _mock_ws(); _connect_authed(gw, ws_carol, carol)

    # Non-participant: even naming an empty room_id must yield no readers.
    await gw._handle_get_message_readers(ws_carol, {"message_id": "m-read", "room_id": ""})
    ev = _events(ws_carol, "message_readers")[-1]
    assert ev["readers"] == [], "non-participant must not see message readers"

    # Participant still gets readers.
    await gw._handle_get_message_readers(ws_alice, {"message_id": "m-read"})
    ev = _events(ws_alice, "message_readers")[-1]
    assert any(r["receiver_webid"] == bob for r in ev["readers"]), "participant must see readers"


# --------------------------------------------------------------------------
# D3: scheduled messages are capped per user.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduled_message_per_user_cap(tmp_path, noauth_env):
    gw = _gw(tmp_path, "a", 9307)
    ws = _mock_ws(); actor = _did(Ed25519PrivateKey.generate())
    await _register(gw, ws, actor)
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    # Pre-fill to the cap directly in the store.
    for i in range(100):
        gw._store.save_scheduled_message({
            "id": f"pre-{i}", "thread_id": "t", "from_webid": actor,
            "content": "x", "send_at": time.time() + 3600, "created_at": time.time(),
        })
    ws.send.reset_mock()
    await gw._handle_schedule_message(ws, {"thread_id": "t", "content": "over", "send_at": future})
    assert _events(ws, "error"), "over-cap scheduling must be rejected"
    assert not _events(ws, "message_scheduled")


@pytest.mark.asyncio
async def test_scheduled_message_under_cap_ok(tmp_path, noauth_env):
    gw = _gw(tmp_path, "a", 9308)
    ws = _mock_ws(); actor = _did(Ed25519PrivateKey.generate())
    await _register(gw, ws, actor)
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    ws.send.reset_mock()
    await gw._handle_schedule_message(ws, {"thread_id": "t", "content": "ok", "send_at": future})
    assert _events(ws, "message_scheduled"), "under-cap scheduling must succeed"


# --------------------------------------------------------------------------
# D4: same-gateway DM typing requires a relationship/participation (auth-on).
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_typing_blocked_to_unrelated_peer_under_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
    gw = _gw(tmp_path, "a", 9309)
    alice = _did(Ed25519PrivateKey.generate())
    carol = _did(Ed25519PrivateKey.generate())
    bob = _did(Ed25519PrivateKey.generate())

    ws_bob = _mock_ws(); _connect_authed(gw, ws_bob, bob)
    ws_alice = _mock_ws(); _connect_authed(gw, ws_alice, alice)
    ws_carol = _mock_ws(); _connect_authed(gw, ws_carol, carol)
    gw._store.save_dm_thread(bob, bob, "Bob", owner_webid=alice)  # alice ↔ bob relationship

    ws_bob.send.reset_mock()
    # Carol has no relationship with Bob → must not deliver.
    await gw._handle_typing(ws_carol, {"cert_id": bob})
    assert not _events(ws_bob, "typing"), "typing to an unrelated same-gateway peer must not deliver"

    ws_bob.send.reset_mock()
    # Alice is a party to the DM → still delivered.
    await gw._handle_typing(ws_alice, {"cert_id": bob})
    assert _events(ws_bob, "typing"), "typing within an existing relationship must deliver"
