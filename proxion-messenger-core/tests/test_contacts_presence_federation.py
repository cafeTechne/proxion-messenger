"""Contacts across two live gateways: presence, renames, read receipts, removal
and ignored friend requests all reach the other side.

Each browser registers under its own client did (as the web app does), not the
gateway did, so delivery has to go through the relationship owner.
"""
import asyncio
import json
import time

import pytest

pytest.importorskip("websockets")
import websockets

from gwharness import free_port, start_gateway

_TIMEOUT = 15.0


@pytest.fixture(autouse=True)
def _local_federation(monkeypatch):
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    monkeypatch.setenv("PROXION_ALLOW_INSECURE_FEDERATION", "1")


def _gateway(tmp_path, name):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState
    ws_port, http_port = free_port(), free_port()
    cfg = GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                        public_url=f"ws://127.0.0.1:{ws_port}",
                        db_path=str(tmp_path / f"{name}.db"))
    agent = AgentState.generate()
    gw = ProxionGateway(agent, {}, {}, cfg, ReadState())
    start_gateway(gw, ws_port, http_port)
    return gw, agent, ws_port, f"http://127.0.0.1:{http_port}"


def _client_did():
    from proxion_messenger_core.didkey import pub_key_to_did
    from proxion_messenger_core.persist import AgentState
    return pub_key_to_did(AgentState.generate().identity_pub_bytes)


async def _wait(conn, pred, timeout=_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            msg = json.loads(await asyncio.wait_for(conn.recv(), timeout=0.3))
        except asyncio.TimeoutError:
            continue
        if pred(msg):
            return msg
    return None


async def _drain(conn):
    while True:
        try:
            await asyncio.wait_for(conn.recv(), timeout=0.15)
        except Exception:
            return


async def _register(conn, did, name):
    await conn.send(json.dumps({"cmd": "register", "did": did, "display_name": name}))
    assert await _wait(conn, lambda m: m.get("type") == "registered")


def _invite(agent_from, http_from):
    from proxion_messenger_core import handshake
    from proxion_messenger_core.federation import Capability
    return handshake.create_invite(
        agent_from.identity_key, agent_from.store_pub_bytes,
        [Capability(with_="stash://dm/", can="crud/write")],
        endpoint_hints=[http_from])


def _post(url, body):
    import httpx
    return httpx.post(url, json=body, timeout=5)


async def _befriend(a, b, conn_a, conn_b):
    """Bob (b) asks Alice (a); Alice accepts. Returns (alice_cert_id, bob_cert_id)."""
    gw_a, agent_a, _, http_a = a
    gw_b, agent_b, _, http_b = b
    invite = _invite(agent_b, http_b)
    gw_b._store.save_pending_invite(invite.to_dict(), gw_a._own_gateway_did())
    assert _post(f"{http_a}/invite", invite.to_dict()).status_code == 200
    assert await _wait(conn_a, lambda m: m.get("type") == "friend_request_received")
    await conn_a.send(json.dumps({"cmd": "accept_friend_request",
                                  "invitation_id": invite.invitation_id}))
    acc = await _wait(conn_a, lambda m: m.get("type") == "friend_request_accepted")
    added = await _wait(conn_b, lambda m: m.get("type") == "contact_added")
    assert acc and added
    await _drain(conn_a)
    await _drain(conn_b)
    return acc["certificate"]["certificate_id"], added["certificate"]["certificate_id"]


@pytest.mark.asyncio
async def test_presence_and_rename_reach_the_contact_on_the_other_gateway(tmp_path):
    a, b = _gateway(tmp_path, "a"), _gateway(tmp_path, "b")
    async with websockets.connect(f"ws://127.0.0.1:{a[2]}") as conn_a, \
               websockets.connect(f"ws://127.0.0.1:{b[2]}") as conn_b:
        await _register(conn_a, _client_did(), "alice")
        await _register(conn_b, _client_did(), "bob")
        await _befriend(a, b, conn_a, conn_b)
        bob_gw_did = b[0]._own_gateway_did()

        await conn_b.send(json.dumps({"cmd": "set_presence", "status": "busy",
                                      "status_message": "at lunch"}))
        ev = await _wait(conn_a, lambda m: m.get("type") == "presence"
                         and m.get("webid") == bob_gw_did and m.get("status") == "busy")
        assert ev is not None, "Alice never saw Bob's status"
        assert ev["status_message"] == "at lunch"

        await conn_b.send(json.dumps({"cmd": "set_identity", "display_name": "Bobby"}))
        ev = await _wait(conn_a, lambda m: m.get("type") == "presence"
                         and m.get("display_name") == "Bobby")
        assert ev is not None, "Alice never learned Bob's new name"
        await conn_a.send(json.dumps({"cmd": "get_relationships"}))
        rels = await _wait(conn_a, lambda m: m.get("type") == "relationships")
        names = [c.get("display_name") for c in rels["contacts"] if c.get("peer_did") == bob_gw_did]
        assert names == ["Bobby"]

    # Bob's last tab closed: Alice's gateway hears he is offline.
    deadline = time.monotonic() + _TIMEOUT
    while time.monotonic() < deadline:
        if a[0]._user_presence.get(bob_gw_did, {}).get("status") == "offline":
            break
        await asyncio.sleep(0.1)
    assert a[0]._user_presence.get(bob_gw_did, {}).get("status") == "offline"


@pytest.mark.asyncio
async def test_opening_the_thread_sends_a_read_receipt_to_the_sender(tmp_path):
    a, b = _gateway(tmp_path, "a"), _gateway(tmp_path, "b")
    async with websockets.connect(f"ws://127.0.0.1:{a[2]}") as conn_a, \
               websockets.connect(f"ws://127.0.0.1:{b[2]}") as conn_b:
        await _register(conn_a, _client_did(), "alice")
        await _register(conn_b, _client_did(), "bob")
        alice_cert, bob_cert = await _befriend(a, b, conn_a, conn_b)

        await conn_a.send(json.dumps({"cmd": "send_dm", "cert_id": alice_cert,
                                      "content": "read me"}))
        echo = await _wait(conn_a, lambda m: m.get("type") == "message"
                           and m.get("content") == "read me")
        got = await _wait(conn_b, lambda m: m.get("type") == "message"
                          and m.get("content") == "read me")
        assert echo and got
        assert got["message_id"] == echo["message_id"]
        await _drain(conn_a)

        # The web app opens a contact's thread with mark_read and no message id.
        await conn_b.send(json.dumps({"cmd": "mark_read", "thread_id": bob_cert}))
        rcpt = await _wait(conn_a, lambda m: m.get("type") == "read_receipt")
        assert rcpt is not None, "Alice never got the read receipt"
        assert rcpt["message_id"] == echo["message_id"]


@pytest.mark.asyncio
async def test_removing_a_contact_ends_it_on_their_gateway_too(tmp_path):
    a, b = _gateway(tmp_path, "a"), _gateway(tmp_path, "b")
    async with websockets.connect(f"ws://127.0.0.1:{a[2]}") as conn_a, \
               websockets.connect(f"ws://127.0.0.1:{b[2]}") as conn_b:
        await _register(conn_a, _client_did(), "alice")
        await _register(conn_b, _client_did(), "bob")
        alice_cert, bob_cert = await _befriend(a, b, conn_a, conn_b)
        alice_gw_did = a[0]._own_gateway_did()

        await conn_a.send(json.dumps({"cmd": "revoke_contact", "cert_id": alice_cert}))
        ev = await _wait(conn_b, lambda m: m.get("type") == "contact_removed_by_peer")
        assert ev is not None, "Bob was never told Alice removed him"
        assert ev["cert_id"] == bob_cert
        assert alice_gw_did in b[0]._revoked_dids

        # Bob's side no longer lists Alice, and a DM to her is refused instead of
        # queueing for a gateway that will never take it.
        await conn_b.send(json.dumps({"cmd": "get_relationships"}))
        rels = await _wait(conn_b, lambda m: m.get("type") == "relationships")
        assert all(c.get("peer_did") != alice_gw_did for c in rels["contacts"])


@pytest.mark.asyncio
async def test_old_removal_notice_does_not_end_a_later_readd(tmp_path):
    a, b = _gateway(tmp_path, "a"), _gateway(tmp_path, "b")
    async with websockets.connect(f"ws://127.0.0.1:{a[2]}") as conn_a, \
               websockets.connect(f"ws://127.0.0.1:{b[2]}") as conn_b:
        await _register(conn_a, _client_did(), "alice")
        await _register(conn_b, _client_did(), "bob")
        await _befriend(a, b, conn_a, conn_b)
        alice_gw_did = a[0]._own_gateway_did()
        # A notice dated before the relationship existed (a replay, or a removal
        # that arrives after a re-add) is ignored.
        stale = {"content_type": "contact_removed", "from_webid": alice_gw_did,
                 "to_webid": b[0]._own_gateway_did(),
                 "timestamp": "2020-01-01T00:00:00+00:00"}
        fut = asyncio.run_coroutine_threadsafe(
            a[0]._relay_ephemeral(b[3], stale), _loop_of(a[0]))
        assert fut.result(10)
        await asyncio.sleep(0.5)
        assert alice_gw_did not in b[0]._revoked_dids


def _loop_of(gw):
    from gwharness import _REGISTRY
    for h in _REGISTRY:
        if h.gw is gw:
            return h.loop
    raise AssertionError("gateway loop not found")


@pytest.mark.asyncio
async def test_ignored_request_stays_gone_and_the_sender_stops_waiting(tmp_path):
    a, b = _gateway(tmp_path, "a"), _gateway(tmp_path, "b")
    async with websockets.connect(f"ws://127.0.0.1:{a[2]}") as conn_a, \
               websockets.connect(f"ws://127.0.0.1:{b[2]}") as conn_b:
        await _register(conn_a, _client_did(), "alice")
        await _register(conn_b, _client_did(), "bob")
        gw_a, gw_b = a[0], b[0]
        invite = _invite(b[1], b[3])
        gw_b._store.save_pending_invite(invite.to_dict(), gw_a._own_gateway_did())
        assert _post(f"{a[3]}/invite", invite.to_dict()).status_code == 200
        assert await _wait(conn_a, lambda m: m.get("type") == "friend_request_received")

        await conn_a.send(json.dumps({"cmd": "decline_friend_request",
                                      "invitation_id": invite.invitation_id}))
        assert await _wait(conn_a, lambda m: m.get("type") == "friend_request_dismissed")
        ev = await _wait(conn_b, lambda m: m.get("type") == "friend_request_dismissed")
        assert ev and ev["invitation_id"] == invite.invitation_id

        # Neither side lists it on the next connect.
        for conn in (conn_a, conn_b):
            await conn.send(json.dumps({"cmd": "list_friend_requests"}))
            fr = await _wait(conn, lambda m: m.get("type") == "friend_requests")
            assert all(p["invitation_id"] != invite.invitation_id for p in fr["pending"])


def test_decline_must_be_signed_by_the_invitee(tmp_path, monkeypatch):
    """A third party cannot cancel someone else's pending request."""
    from datetime import datetime, timezone
    import base64
    from proxion_messenger_core.didkey import pub_key_to_did
    from proxion_messenger_core.gateway import invite_decline_signing_input
    from proxion_messenger_core.persist import AgentState
    b = _gateway(tmp_path, "b")
    target = AgentState.generate()
    target_did = pub_key_to_did(target.identity_pub_bytes)
    invite = _invite(b[1], b[3])
    b[0]._store.save_pending_invite(invite.to_dict(), target_did)

    mallory = AgentState.generate()
    mallory_did = pub_key_to_did(mallory.identity_pub_bytes)
    ts = datetime.now(timezone.utc).isoformat()

    def body(signer, claimed):
        sig = signer.identity_key.sign(
            invite_decline_signing_input(invite.invitation_id, claimed, ts))
        return {"invitation_id": invite.invitation_id, "from_did": claimed, "timestamp": ts,
                "signature": base64.urlsafe_b64encode(sig).rstrip(b"=").decode()}

    # Forged signature for the target, and a valid one from the wrong party.
    assert _post(f"{b[3]}/invite/decline", body(mallory, target_did)).status_code == 400
    assert _post(f"{b[3]}/invite/decline", body(mallory, mallory_did)).status_code == 200
    assert b[0]._store.get_pending_invite_row(invite.invitation_id)["status"] == "pending"
    assert _post(f"{b[3]}/invite/decline", body(target, target_did)).status_code == 200
    assert b[0]._store.get_pending_invite_row(invite.invitation_id)["status"] == "declined"
