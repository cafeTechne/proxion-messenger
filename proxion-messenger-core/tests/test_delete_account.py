""""Delete identity" erases the account on the gateway, not just in the browser.

Before, the button wiped the browser and left the account's DM history, contacts,
keys and room memberships on the gateway. The delete_account command removes
them, only for the proven account itself, from its primary session.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
from unittest.mock import AsyncMock

import pytest
import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gwharness import free_port, start_gateway
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState

BOB = "did:key:zBob"
CAROL = "did:key:zCarol"


def _did(priv):
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _seed(store: LocalStore, who: str, peer: str, tag: str) -> None:
    store.save_dm_thread(f"dm-{tag}", peer, "Peer", owner_webid=who)
    store.save_message(f"m-{tag}", f"dm-{tag}", "local_dm", who, "Me", f"secret {tag}",
                       "2026-10-01T00:00:00+00:00")
    store.save_pin(f"dm-{tag}", f"m-{tag}", who, "x")
    store.save_reaction(f"dm-{tag}", f"m-{tag}", "+1", who)
    store.set_dm_disappear_timer(f"dm-{tag}", 60000)
    store.set_block(who, "did:key:zBlocked", True)
    store.set_thread_mute(who, peer, True)
    store.save_scheduled_message({"id": f"s-{tag}", "thread_id": f"dm-{tag}", "from_webid": who,
                                  "content": "later", "send_at": 9999999999, "created_at": 1})


def _count(store: LocalStore, sql: str, *args) -> int:
    c = sqlite3.connect(store.db_path)
    try:
        return c.execute(sql, args).fetchone()[0]
    finally:
        c.close()


def test_store_deletes_one_account_and_keeps_the_other(tmp_path):
    s = LocalStore(str(tmp_path / "s.db"))
    alice = "did:key:zAlice"
    _seed(s, alice, BOB, "a")
    _seed(s, CAROL, BOB, "c")
    s.delete_account_data(alice)

    assert s.get_dm_threads(owner_webid=alice) == []
    assert _count(s, "SELECT COUNT(*) FROM messages WHERE thread_id='dm-a'") == 0
    assert _count(s, "SELECT COUNT(*) FROM messages_fts WHERE thread_id='dm-a'") == 0
    assert s.get_pins("dm-a") == [] and s.get_reactions("dm-a") == []
    assert s.get_dm_disappear_timer("dm-a") in (0, None)
    assert not s.is_blocked_by(alice, "did:key:zBlocked")
    assert s.get_muted_keys(alice) == []
    assert s.get_scheduled_messages_for_user(alice) == []

    # Carol's data on the same gateway is untouched.
    assert [t["thread_id"] for t in s.get_dm_threads(owner_webid=CAROL)] == ["dm-c"]
    assert _count(s, "SELECT COUNT(*) FROM messages WHERE thread_id='dm-c'") == 1
    assert s.is_blocked_by(CAROL, "did:key:zBlocked")
    assert [m["id"] for m in s.get_scheduled_messages_for_user(CAROL)] == ["s-c"]


def _mock_ws():
    ws = AsyncMock()
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _sent(ws):
    return [json.loads(c[0][0]) for c in ws.send.call_args_list]


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    return ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                          config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "g.db")))


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


@pytest.mark.asyncio
async def test_delete_account_leaves_rooms_and_erases_the_account(gateway):
    alice = _did(Ed25519PrivateKey.generate())
    carol = _did(Ed25519PrivateKey.generate())
    ws_a, ws_c = _mock_ws(), _mock_ws()
    await _register(gateway, ws_a, alice)
    await _register(gateway, ws_c, carol)
    _seed(gateway._store, alice, BOB, "a")

    await gateway.process_command(ws_a, {"cmd": "chat_room_create", "name": "Club"})
    room_id = next(e["room_id"] for e in _sent(ws_a) if e.get("room_id"))
    gateway._local_rooms[room_id]["members"].add(ws_c)
    gateway._store.add_room_member(room_id, carol)

    # Refused without the explicit confirm.
    await gateway.process_command(ws_a, {"cmd": "delete_account"})
    assert gateway._store.get_dm_threads(owner_webid=alice)

    await gateway.process_command(ws_a, {"cmd": "delete_account", "confirm": "delete"})
    assert any(e.get("type") == "account_deleted" for e in _sent(ws_a))
    assert gateway._store.get_dm_threads(owner_webid=alice) == []
    assert _count(gateway._store, "SELECT COUNT(*) FROM messages WHERE thread_id='dm-a'") == 0
    # The room lives on with Carol as owner; Alice is no longer a member.
    assert gateway._local_rooms[room_id]["creator_webid"] == carol
    assert alice not in gateway._store.get_room_members(room_id)
    # Every session of the account is closed.
    assert ws_a in gateway._revoked_sessions


@pytest.mark.asyncio
async def test_linked_device_and_gateway_identity_cannot_erase(gateway):
    acct = _did(Ed25519PrivateKey.generate())
    ws = _mock_ws()
    await _register(gateway, ws, acct)
    _seed(gateway._store, acct, BOB, "x")
    gateway._session_device_did[ws] = _did(Ed25519PrivateKey.generate())
    await gateway.process_command(ws, {"cmd": "delete_account", "confirm": "delete"})
    assert any(e.get("message") == "linked_device" for e in _sent(ws))
    assert gateway._store.get_dm_threads(owner_webid=acct)

    gw_did = pub_key_to_did(gateway.agent.identity_pub_bytes)
    ws2 = _mock_ws()
    await _register(gateway, ws2, gw_did)
    await gateway.process_command(ws2, {"cmd": "delete_account", "confirm": "delete"})
    assert any(e.get("message") == "gateway_identity" for e in _sent(ws2))


async def _recv_until(ws, pred, timeout=5.0):
    async def _loop():
        while True:
            m = json.loads(await ws.recv())
            if pred(m):
                return m
    return await asyncio.wait_for(_loop(), timeout)


def test_wire_needs_a_proven_identity(tmp_path, monkeypatch):
    """Over a real socket with auth enforced (as over the tunnel): an unproven
    socket is refused, and a proven one erases only its own account."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
    ws_port, http_port = free_port(), free_port()
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                                             db_path=str(tmp_path / "w.db")))
    start_gateway(gw, ws_port, http_port)
    key = Ed25519PrivateKey.generate()
    alice = _did(key)
    _seed(gw._store, alice, BOB, "a")
    _seed(gw._store, CAROL, BOB, "c")

    async def run():
        url = f"ws://127.0.0.1:{ws_port}"
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"cmd": "delete_account", "confirm": "delete"}))
            m = await _recv_until(ws, lambda m: m.get("type") == "error")
            assert m["message"] == "Not registered"
            # A register without answering the challenge is not enough either.
            await ws.send(json.dumps({"cmd": "register", "did": alice, "display_name": "A"}))
            await _recv_until(ws, lambda m: m.get("type") == "auth_challenge")
            await ws.send(json.dumps({"cmd": "delete_account", "confirm": "delete"}))
            m = await _recv_until(ws, lambda m: m.get("type") == "error")
            assert m["message"] == "Not registered"
        assert gw._store.get_dm_threads(owner_webid=alice)

        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"cmd": "register", "did": alice, "display_name": "A"}))
            ch = await _recv_until(ws, lambda m: m.get("type") == "auth_challenge")
            sig = base64.urlsafe_b64encode(key.sign(ch["nonce"].encode())).rstrip(b"=").decode()
            await ws.send(json.dumps({"cmd": "auth_response", "nonce": ch["nonce"], "signature": sig}))
            await _recv_until(ws, lambda m: m.get("type") == "registered")
            await ws.send(json.dumps({"cmd": "delete_account", "confirm": "delete"}))
            await _recv_until(ws, lambda m: m.get("type") == "account_deleted")

    asyncio.run(run())
    assert gw._store.get_dm_threads(owner_webid=alice) == []
    assert [t["thread_id"] for t in gw._store.get_dm_threads(owner_webid=CAROL)] == ["dm-c"]
