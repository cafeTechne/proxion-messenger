"""Friend requests between two live gateways: a sent request never shows as
incoming, a request is accepted at most once, two requests between the same
people end in one relationship, a block of a stranger keeps their requests out,
an ignore reaches a sender whose gateway was down, and reading a thread clears
it on the reader's other sessions.

Browsers register under their own client did, as the web app does.
"""
import asyncio
import json

import pytest

pytest.importorskip("websockets")
import websockets

from test_contacts_presence_federation import (
    _gateway, _client_did, _wait, _drain, _register, _invite, _post,
)


@pytest.fixture(autouse=True)
def _local_federation(monkeypatch):
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    monkeypatch.setenv("PROXION_ALLOW_INSECURE_FEDERATION", "1")


def _addr(g):
    return f"{g[0]._own_gateway_did()}@{g[3]}"


async def _registered(port, did, name):
    """Connect and register; returns (conn, frames pushed during register)."""
    conn = await websockets.connect(f"ws://127.0.0.1:{port}")
    await conn.send(json.dumps({"cmd": "register", "did": did, "display_name": name}))
    frames = []
    while True:
        try:
            m = json.loads(await asyncio.wait_for(conn.recv(), timeout=0.6))
        except asyncio.TimeoutError:
            if any(f.get("type") == "registered" for f in frames):
                return conn, frames
            continue
        frames.append(m)


def _pending_in(frames):
    return [p for f in frames if f.get("type") == "friend_requests" for p in f.get("pending", [])]


def _rels_with(gw, peer_did):
    return [r for r in gw._store.list_relationships() if r.get("peer_did") == peer_did]


@pytest.mark.asyncio
async def test_own_sent_request_is_not_incoming_and_cannot_be_accepted(tmp_path):
    a, b = _gateway(tmp_path, "a"), _gateway(tmp_path, "b")
    alice = _client_did()
    async with websockets.connect(f"ws://127.0.0.1:{a[2]}") as conn_a:
        await _register(conn_a, alice, "alice")
        await conn_a.send(json.dumps({"cmd": "send_friend_request", "target_address": _addr(b)}))
        sent = await _wait(conn_a, lambda m: m.get("type") == "friend_request_sent")
        assert sent
    inv_id = sent["invitation_id"]

    conn, frames = await _registered(a[2], alice, "alice")
    try:
        assert all(p["invitation_id"] != inv_id for p in _pending_in(frames))
        await conn.send(json.dumps({"cmd": "accept_friend_request", "invitation_id": inv_id}))
        err = await _wait(conn, lambda m: m.get("type") in ("error", "friend_request_accepted"))
        assert err["type"] == "error" and err["message"] == "invite_not_found"
        assert err["invitation_id"] == inv_id
    finally:
        await conn.close()
    assert a[0]._store.list_relationships() == []
    assert a[0]._store.get_pending_invite_row(inv_id)["status"] == "pending"


@pytest.mark.asyncio
async def test_list_friend_requests_keeps_sent_apart_from_received(tmp_path):
    from unittest.mock import AsyncMock
    a = _gateway(tmp_path, "a")
    gw = a[0]
    peer = _gateway(tmp_path, "p")
    outbound = _invite(a[1], a[3])
    gw._store.save_pending_invite(outbound.to_dict(), "did:key:zTarget")
    inbound = _invite(peer[1], peer[3])
    gw._store.save_pending_invite(inbound.to_dict(), peer[0]._own_gateway_did())
    ws = AsyncMock()
    gw._client_webids[ws] = gw._own_gateway_did()
    await gw._handle_list_friend_requests(ws, {})
    resp = json.loads(ws.send.call_args[0][0])
    assert [p["invitation_id"] for p in resp["pending"]] == [inbound.invitation_id]
    assert resp["pending"][0]["from_did"] == peer[0]._own_gateway_did()
    assert [s["invitation_id"] for s in resp["sent"]] == [outbound.invitation_id]
    assert resp["sent"][0]["target_did"] == "did:key:zTarget"


@pytest.mark.asyncio
async def test_a_request_is_accepted_at_most_once(tmp_path):
    a, b = _gateway(tmp_path, "a"), _gateway(tmp_path, "b")
    async with websockets.connect(f"ws://127.0.0.1:{a[2]}") as conn_a, \
               websockets.connect(f"ws://127.0.0.1:{b[2]}") as conn_b:
        await _register(conn_a, _client_did(), "alice")
        await _register(conn_b, _client_did(), "bob")
        alice_gw = a[0]._own_gateway_did()

        # Ignored, then a stale card is accepted: refused, nobody is added.
        await conn_a.send(json.dumps({"cmd": "send_friend_request", "target_address": _addr(b)}))
        r = await _wait(conn_b, lambda m: m.get("type") == "friend_request_received")
        await conn_b.send(json.dumps({"cmd": "decline_friend_request",
                                      "invitation_id": r["invitation_id"]}))
        assert await _wait(conn_b, lambda m: m.get("type") == "friend_request_dismissed")
        await conn_b.send(json.dumps({"cmd": "accept_friend_request",
                                      "invitation_id": r["invitation_id"]}))
        err = await _wait(conn_b, lambda m: m.get("type") in ("error", "friend_request_accepted"))
        assert err["type"] == "error" and err["message"] == "invite_not_found"
        assert _rels_with(b[0], alice_gw) == []

        # A double-click on Accept: one relationship.
        await conn_a.send(json.dumps({"cmd": "send_friend_request", "target_address": _addr(b)}))
        r = await _wait(conn_b, lambda m: m.get("type") == "friend_request_received")
        for _ in range(2):
            await conn_b.send(json.dumps({"cmd": "accept_friend_request",
                                          "invitation_id": r["invitation_id"]}))
        first = await _wait(conn_b, lambda m: m.get("type") in ("error", "friend_request_accepted"))
        second = await _wait(conn_b, lambda m: m.get("type") in ("error", "friend_request_accepted"))
        assert first["type"] == "friend_request_accepted"
        assert second["type"] == "error" and second["message"] == "invite_not_found"
        assert len(_rels_with(b[0], alice_gw)) == 1


@pytest.mark.asyncio
async def test_requests_both_ways_end_in_one_relationship_each(tmp_path):
    a, b = _gateway(tmp_path, "a"), _gateway(tmp_path, "b")
    alice = _client_did()
    async with websockets.connect(f"ws://127.0.0.1:{a[2]}") as conn_a, \
               websockets.connect(f"ws://127.0.0.1:{b[2]}") as conn_b:
        await _register(conn_a, alice, "alice")
        await _register(conn_b, _client_did(), "bob")
        await conn_a.send(json.dumps({"cmd": "send_friend_request", "target_address": _addr(b)}))
        to_bob = await _wait(conn_b, lambda m: m.get("type") == "friend_request_received")
        await conn_b.send(json.dumps({"cmd": "send_friend_request", "target_address": _addr(a)}))
        to_alice = await _wait(conn_a, lambda m: m.get("type") == "friend_request_received")
        assert to_bob and to_alice

        await conn_b.send(json.dumps({"cmd": "accept_friend_request",
                                      "invitation_id": to_bob["invitation_id"]}))
        # Alice is added, and Bob's own request is settled on her side: its card goes.
        seen = set()

        def _note(m):
            if m.get("type") == "contact_added":
                seen.add("added")
            if (m.get("type") == "friend_request_dismissed"
                    and m.get("invitation_id") == to_alice["invitation_id"]):
                seen.add("dismissed")
            return seen == {"added", "dismissed"}
        assert await _wait(conn_a, _note), seen
        await conn_a.send(json.dumps({"cmd": "accept_friend_request",
                                      "invitation_id": to_alice["invitation_id"]}))
        err = await _wait(conn_a, lambda m: m.get("type") in ("error", "friend_request_accepted"))
        assert err["type"] == "error"
        await _drain(conn_a)
        await _drain(conn_b)

    assert len(_rels_with(a[0], b[0]._own_gateway_did())) == 1
    assert len(_rels_with(b[0], a[0]._own_gateway_did())) == 1
    conn, frames = await _registered(a[2], alice, "alice")
    await conn.close()
    assert _pending_in(frames) == []


@pytest.mark.asyncio
async def test_register_snapshot_lists_each_contact_once(tmp_path):
    from proxion_messenger_core.federation import RelationshipCertificate, Capability
    a, peer = _gateway(tmp_path, "a"), _gateway(tmp_path, "p")
    gw = a[0]
    peer_did = peer[0]._own_gateway_did()
    cert_ids = []
    for _ in range(2):
        cert = RelationshipCertificate(
            issuer=a[1].identity_pub_bytes.hex(), subject=peer[1].identity_pub_bytes.hex(),
            capabilities=[Capability(with_="stash://dm/", can="crud/write")])
        cert.sign(a[1].identity_key)
        gw._store.save_relationship(cert.to_dict(), peer_did=peer_did)
        cert_ids.append(cert.certificate_id)
        await asyncio.sleep(1.1)   # created_at has one-second resolution
    conn, frames = await _registered(a[2], _client_did(), "alice")
    await conn.close()
    contacts = [c for f in frames if f.get("type") == "relationships" for c in f["contacts"]]
    assert [c["certificate_id"] for c in contacts if c["peer_did"] == peer_did] == [cert_ids[1]]


@pytest.mark.asyncio
async def test_blocking_a_stranger_keeps_their_requests_out(tmp_path):
    a, b = _gateway(tmp_path, "a"), _gateway(tmp_path, "b")
    async with websockets.connect(f"ws://127.0.0.1:{a[2]}") as conn_a, \
               websockets.connect(f"ws://127.0.0.1:{b[2]}") as conn_b:
        await _register(conn_a, _client_did(), "alice")
        await _register(conn_b, _client_did(), "bob")
        await conn_b.send(json.dumps({"cmd": "block", "webid": a[0]._own_gateway_did()}))
        await asyncio.sleep(0.3)
        await conn_a.send(json.dumps({"cmd": "send_friend_request", "target_address": _addr(b)}))
        assert await _wait(conn_a, lambda m: m.get("type") == "friend_request_sent")
        got = await _wait(conn_b, lambda m: m.get("type") == "friend_request_received", timeout=2)
        assert got is None
    assert b[0]._store.list_pending_invites("pending") == []


@pytest.mark.asyncio
async def test_ignore_is_queued_while_the_sender_is_down(tmp_path, monkeypatch):
    from proxion_messenger_core import network
    from proxion_messenger_core.gateway import invite_decline_signing_input
    a, b = _gateway(tmp_path, "a"), _gateway(tmp_path, "b")
    gw_a = a[0]
    invite = _invite(b[1], b[3])
    gw_a._store.save_pending_invite(invite.to_dict(), b[0]._own_gateway_did())

    posts = []

    async def down(url, payload, **kw):
        posts.append((url, payload))
        return False
    monkeypatch.setattr(network, "async_safe_post", down)
    assert await gw_a._send_invite_decline(b[3], invite.invitation_id,
                                           b[0]._own_gateway_did()) is False
    rows = gw_a._store.get_pending_relays()
    assert [r["id"] for r in rows] == [f"invite_decline:{invite.invitation_id}"]

    # The sender's gateway is back: the retry reaches /invite/decline, signed.
    async def up(url, payload, **kw):
        posts.append((url, payload))
        return True
    monkeypatch.setattr(network, "async_safe_post", up)
    assert await gw_a._attempt_pending_relay(rows[0]) is True
    url, body = posts[-1]
    assert url == b[3] + "/invite/decline"
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    import base64
    sig = base64.urlsafe_b64decode(body["signature"] + "=" * (-len(body["signature"]) % 4))
    Ed25519PublicKey.from_public_bytes(a[1].identity_pub_bytes).verify(
        sig, invite_decline_signing_input(invite.invitation_id, body["from_did"], body["timestamp"]))
    assert gw_a._store.get_pending_relays() == []


@pytest.mark.asyncio
async def test_reading_a_thread_clears_it_on_the_readers_other_sessions(tmp_path):
    a = _gateway(tmp_path, "a")
    me = _client_did()
    async with websockets.connect(f"ws://127.0.0.1:{a[2]}") as tab1, \
               websockets.connect(f"ws://127.0.0.1:{a[2]}") as tab2:
        await _register(tab1, me, "me")
        await _register(tab2, me, "me")
        await _drain(tab1)
        await _drain(tab2)
        await tab1.send(json.dumps({"cmd": "update_last_read", "channel_id": "thread-x"}))
        ev = await _wait(tab2, lambda m: m.get("type") == "thread_read", timeout=3)
        assert ev and ev["thread_id"] == "thread-x"
        assert await _wait(tab1, lambda m: m.get("type") == "thread_read", timeout=1) is None
