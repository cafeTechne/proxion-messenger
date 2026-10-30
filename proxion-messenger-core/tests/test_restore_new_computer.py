"""Restoring on a new computer: recovery kit, then the data export.

Alice and Bob are contacts on gateways A1 and B. A1 goes away; Alice restores
her kit into a fresh gateway A2 (new URL, new browser identity) and imports her
export. Afterwards both sides must reach each other, her room must come back,
her own old messages must still be hers, and the running gateway must sign as
the restored identity without a restart.
"""
import asyncio
import json
import time

import httpx
import pytest
import websockets

from gwharness import free_port, start_gateway
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState

T = 10.0
PASS = "pp-1234567890"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("PROXION_CSS_URL", "PROXION_CSS_EMAIL", "PROXION_CSS_PASSWORD"):
        monkeypatch.setenv(k, "")
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    monkeypatch.setenv("PROXION_ALLOW_INSECURE_FEDERATION", "1")


def _gateway(tmp_path, name):
    from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
    ws_port, http_port = free_port(), free_port()
    (tmp_path / name).mkdir()
    cfg = GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                        public_url=f"ws://127.0.0.1:{ws_port}",
                        db_path=str(tmp_path / name / "proxion.db"))
    agent = AgentState.generate()
    gw = ProxionGateway(agent, {}, {}, cfg, ReadState())
    h = start_gateway(gw, ws_port, http_port)
    return gw, agent, ws_port, f"http://127.0.0.1:{http_port}", h


def _client_did():
    return pub_key_to_did(AgentState.generate().identity_pub_bytes)


async def _wait(conn, pred, timeout=T, log=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            m = json.loads(await asyncio.wait_for(conn.recv(), timeout=0.3))
        except asyncio.TimeoutError:
            continue
        if log is not None:
            log.append(m)
        if pred(m):
            return m
    return None


async def _drain(conn, log=None):
    while True:
        try:
            m = json.loads(await asyncio.wait_for(conn.recv(), timeout=0.3))
        except Exception:
            return
        if log is not None:
            log.append(m)


async def _reg(conn, did, name=None):
    p = {"cmd": "register", "did": did}
    if name:
        p["display_name"] = name
    await conn.send(json.dumps(p))
    log = []
    assert await _wait(conn, lambda m: m.get("type") == "registered", log=log), log
    await _drain(conn, log)
    return log


async def _befriend(a, b, conn_a, conn_b):
    from proxion_messenger_core import handshake
    from proxion_messenger_core.federation import Capability
    gw_a, _, _, http_a, _ = a
    gw_b, agent_b, _, http_b, _ = b
    invite = handshake.create_invite(agent_b.identity_key, agent_b.store_pub_bytes,
                                     [Capability(with_="stash://dm/", can="crud/write")],
                                     endpoint_hints=[http_b])
    gw_b._store.save_pending_invite(invite.to_dict(), gw_a._own_gateway_did())
    assert httpx.post(f"{http_a}/invite", json=invite.to_dict()).status_code == 200
    assert await _wait(conn_a, lambda m: m.get("type") == "friend_request_received")
    await conn_a.send(json.dumps({"cmd": "accept_friend_request", "invitation_id": invite.invitation_id}))
    acc = await _wait(conn_a, lambda m: m.get("type") == "friend_request_accepted")
    added = await _wait(conn_b, lambda m: m.get("type") == "contact_added")
    await _drain(conn_a)
    await _drain(conn_b)
    return acc["certificate"]["certificate_id"], added["certificate"]["certificate_id"]


async def _ticket(conn, purpose):
    await conn.send(json.dumps({"cmd": "export_ticket", "purpose": purpose}))
    return (await _wait(conn, lambda m: m.get("type") == "export_ticket"))["ticket"]


def _import(http, ticket, body):
    return httpx.post(f"{http}/import?ticket={ticket}", content=body,
                      headers={"Content-Type": "application/json", "Origin": http})


def test_restore_then_import_on_new_computer(tmp_path):
    asyncio.run(_flow(tmp_path))


async def _flow(tmp_path):
    a1 = _gateway(tmp_path, "a1")
    b = _gateway(tmp_path, "b")
    ca1, cb = _client_did(), _client_did()
    async with websockets.connect(f"ws://127.0.0.1:{a1[2]}", max_size=None) as conn_a, \
            websockets.connect(f"ws://127.0.0.1:{b[2]}", max_size=None) as conn_b:
        await _reg(conn_a, ca1, "Alice")
        await _reg(conn_b, cb, "Bob")
        a_cert, b_cert = await _befriend(a1, b, conn_a, conn_b)
        await conn_a.send(json.dumps({"cmd": "send_dm", "cert_id": a_cert, "content": "hi bob from A1"}))
        assert await _wait(conn_b, lambda m: m.get("content") == "hi bob from A1")
        await conn_a.send(json.dumps({"cmd": "chat_room_create", "name": "Book club"}))
        assert await _wait(conn_a, lambda m: m.get("type") in ("room_created", "local_room_created"))
        await _drain(conn_a)
        kit = httpx.get(f"{a1[3]}/backup", headers={"x-proxion-passphrase": PASS, "Origin": a1[3]})
        assert kit.status_code == 200
        exp = httpx.get(f"{a1[3]}/export?ticket={await _ticket(conn_a, 'export')}",
                        headers={"Origin": a1[3]})
        assert exp.status_code == 200
    alice_did = a1[0]._own_gateway_did()
    a1[4].stop()

    a2 = _gateway(tmp_path, "a2")
    ca2 = _client_did()
    async with websockets.connect(f"ws://127.0.0.1:{a2[2]}", max_size=None) as conn:
        await _reg(conn, ca2)
        # Importing before the kit would drop every contact: refused, not silent.
        early = _import(a2[3], await _ticket(conn, "import"), exp.content)
        assert early.status_code == 409 and early.json()["error"] == "restore_kit_first"
        old_seal = a2[0]._own_x25519_pub_b64
        r = httpx.post(f"{a2[3]}/restore", content=kit.content,
                       headers={"x-proxion-passphrase": PASS, "Content-Type": "application/json",
                                "Origin": a2[3]})
        assert r.status_code == 200, r.text
    # The running gateway is the restored identity now, no restart needed.
    assert a2[0]._own_gateway_did() == alice_did
    assert a2[0]._own_x25519_pub_b64 != old_seal
    assert a2[0]._store.get_gateway_seal_key(alice_did) == a2[0]._own_x25519_pub_b64

    async with websockets.connect(f"ws://127.0.0.1:{a2[2]}", max_size=None) as conn:
        await _reg(conn, ca2, "Alice")
        imp = _import(a2[3], await _ticket(conn, "import"), exp.content)
        assert imp.status_code == 200, imp.text
        counts = imp.json()["counts"]
        assert counts["relationships"] == 1
        assert counts["rooms"] == 1
        assert counts["peer_gateways"] == 1
        await _drain(conn)

    async with websockets.connect(f"ws://127.0.0.1:{a2[2]}", max_size=None) as conn, \
            websockets.connect(f"ws://127.0.0.1:{b[2]}", max_size=None) as conn_b:
        log = await _reg(conn, ca2)
        await _reg(conn_b, cb)
        rooms = [m for m in log if m.get("type") == "rooms"]
        assert rooms and [r.get("name") for r in rooms[0]["rooms"]] == ["Book club"]
        rels = [m for m in log if m.get("type") == "relationships"][0]["contacts"]
        await conn.send(json.dumps({"cmd": "send_dm", "cert_id": rels[0]["certificate_id"],
                                    "content": "hello from new computer"}))
        assert await _wait(conn_b, lambda m: m.get("content") == "hello from new computer")
        # The presence relay is signed by the restored identity and names its
        # new address, so Bob's gateway follows Alice to A2.
        await conn.send(json.dumps({"cmd": "set_presence", "status": "busy", "status_message": "moved"}))
        assert await _wait(conn_b, lambda m: m.get("type") == "presence"
                           and m.get("status_message") == "moved")
        pin = b[0]._store.get_peer_gateway_pin(alice_did)
        assert pin["pinned_gateway_url"] == a2[3] and not pin["pending_change"]
        await conn_b.send(json.dumps({"cmd": "send_dm", "cert_id": b_cert, "content": "are you there?"}))
        assert await _wait(conn, lambda m: m.get("content") == "are you there?")
        # Alice's own old messages are still hers.
        thread = [m for m in log if m.get("type") == "local_dms"][0]["dms"][0]["id"]
        await conn.send(json.dumps({"cmd": "get_local_history", "thread_id": thread, "limit": 50}))
        hist = await _wait(conn, lambda m: m.get("type") == "local_history")
        mine = [x for x in hist["messages"] if x.get("content") == "hi bob from A1"]
        assert mine and mine[0]["from_webid"] == ca2


def test_delete_account_tells_contacts(tmp_path):
    """Reset ends each relationship on the contact's side too."""
    async def run():
        a, b = _gateway(tmp_path, "ra"), _gateway(tmp_path, "rb")
        async with websockets.connect(f"ws://127.0.0.1:{a[2]}") as conn_a, \
                websockets.connect(f"ws://127.0.0.1:{b[2]}") as conn_b:
            await _reg(conn_a, _client_did(), "Alice")
            await _reg(conn_b, _client_did(), "Bob")
            await _befriend(a, b, conn_a, conn_b)
            await conn_a.send(json.dumps({"cmd": "delete_account", "confirm": "delete"}))
            assert await _wait(conn_a, lambda m: m.get("type") == "account_deleted")
            assert await _wait(conn_b, lambda m: m.get("type") == "contact_removed_by_peer")
            await conn_b.send(json.dumps({"cmd": "get_relationships"}))
            rels = await _wait(conn_b, lambda m: m.get("type") == "relationships")
            assert rels["contacts"] == []
    asyncio.run(run())


def test_unproven_gateway_change_stays_pending(tmp_path):
    """A URL change without a signed claim never redirects a contact's traffic."""
    from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
    cfg = GatewayConfig(host="127.0.0.1", port=0, http_port=0,
                        db_path=str(tmp_path / "proxion.db"))
    gw = ProxionGateway(AgentState.generate(), {}, {}, cfg, ReadState())
    peer = pub_key_to_did(AgentState.generate().identity_pub_bytes)

    async def run():
        gw._record_peer_gateway(peer, "https://old.example")
        gw._store.enqueue_relay("r1", peer, "https://old.example", {"to_webid": peer})
        gw._record_peer_gateway(peer, "https://evil.example")
        pin = gw._store.get_peer_gateway_pin(peer)
        assert pin["pinned_gateway_url"] == "https://old.example" and pin["pending_change"]
        assert gw._resolve_peer_gateway(peer) == "https://old.example"
        gw._record_peer_gateway(peer, "https://new.example", proven=True)
        pin = gw._store.get_peer_gateway_pin(peer)
        assert pin["pinned_gateway_url"] == "https://new.example" and not pin["pending_change"]
        assert gw._resolve_peer_gateway(peer) == "https://new.example"
        assert [r["to_gateway_url"] for r in gw._store.get_pending_relays()] == ["https://new.example"]
        await asyncio.sleep(0)
    asyncio.run(run())


def test_gateway_url_claim_binds_did_and_url():
    from proxion_messenger_core.relay import sign_gateway_url_claim, verify_gateway_url_claim
    alice, mallory = AgentState.generate(), AgentState.generate()
    did = pub_key_to_did(alice.identity_pub_bytes)
    claim = sign_gateway_url_claim(alice.identity_key, did, "https://new.example")
    assert verify_gateway_url_claim(did, "https://new.example", claim)
    assert not verify_gateway_url_claim(did, "https://evil.example", claim)
    forged = sign_gateway_url_claim(mallory.identity_key, did, "https://evil.example")
    assert not verify_gateway_url_claim(did, "https://evil.example", forged)
    stale = dict(claim, origin_gateway_url_ts="2020-01-01T00:00:00+00:00")
    assert not verify_gateway_url_claim(did, "https://new.example", stale)
