"""Several accounts on one gateway: DM threads and /export stay per account.

The web client names a same-gateway DM thread from the peer alone
("local-<peer>"), so two accounts DMing the same peer used to share one thread:
the second account's messages landed in the first account's conversation, each
could read the other's history, and dm_threads (keyed by thread_id only) kept
just the first account's row.
"""
import asyncio
import json
import re
import socket
import sqlite3
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gwharness import start_gateway
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState


def _did():
    return pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _nav_id(did):
    # What web/main.js uses as the thread id of a DM with *did*.
    return "local-" + re.sub(r"[^a-zA-Z0-9]", "-", did)


def _ws():
    ws = AsyncMock()
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _last(ws, typ):
    for c in reversed(ws.send.call_args_list):
        m = json.loads(c[0][0])
        if m.get("type") == typ:
            return m
    return None


def _gateway(tmp_path, **kw):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "rooms.db"), **kw),
    )


async def _three_accounts(gw):
    socks = {}
    for name in ("a", "b", "c"):
        ws, d = _ws(), _did()
        gw.clients.add(ws)
        await gw.process_command(ws, {"cmd": "register", "did": d, "display_name": name})
        socks[name] = (ws, d)
    return socks


async def _history(gw, ws, thread_id):
    ws.send.reset_mock()
    await gw.process_command(ws, {"cmd": "get_local_history", "thread_id": thread_id})
    return [m["content"] for m in _last(ws, "local_history")["messages"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("auth", [False, True])
async def test_two_accounts_dming_same_peer_get_separate_threads(tmp_path, monkeypatch, auth):
    gw = _gateway(tmp_path)
    s = await _three_accounts(gw)
    (a, ad), (b, bd), (c, cd) = s["a"], s["b"], s["c"]
    if auth:
        monkeypatch.setattr(type(gw), "_auth_enforced", lambda self: True)
    tid = _nav_id(bd)

    await gw.process_command(a, {"cmd": "local_dm", "target_webid": bd,
                                 "thread_id": tid, "content": "from A (private)"})
    c.send.reset_mock()
    await gw.process_command(c, {"cmd": "local_dm", "target_webid": bd,
                                 "thread_id": tid, "content": "from C"})

    # A keeps the id it picked; C's message is filed under C's own thread.
    echo = _last(c, "message")
    c_tid = echo["thread_id"]
    assert c_tid != tid
    assert echo["client_thread_id"] == tid
    assert [t["thread_id"] for t in gw._store.get_dm_threads(owner_webid=ad)] == [tid]
    assert [t["thread_id"] for t in gw._store.get_dm_threads(owner_webid=cd)] == [c_tid]

    # Neither account reads the other's conversation.
    assert await _history(gw, a, tid) == ["from A (private)"]
    assert await _history(gw, c, tid) == []
    assert await _history(gw, c, c_tid) == ["from C"]
    assert await _history(gw, a, c_tid) == []

    # The peer is party to both and can read each.
    assert await _history(gw, b, c_tid) == ["from C"]
    assert await _history(gw, b, tid) == ["from A (private)"]

    # C keeps sending with the client id; it still lands in C's thread.
    await gw.process_command(c, {"cmd": "local_dm", "target_webid": bd,
                                 "thread_id": tid, "content": "again from C"})
    assert await _history(gw, c, c_tid) == ["from C", "again from C"]
    assert await _history(gw, a, tid) == ["from A (private)"]


@pytest.mark.asyncio
async def test_peer_replying_in_the_thread_owns_it_too(tmp_path):
    gw = _gateway(tmp_path)
    s = await _three_accounts(gw)
    (a, ad), (b, bd) = s["a"], s["b"]
    tid = _nav_id(bd)
    await gw.process_command(a, {"cmd": "local_dm", "target_webid": bd,
                                 "thread_id": tid, "content": "hi B"})
    b.send.reset_mock()
    await gw.process_command(b, {"cmd": "local_dm", "target_webid": ad,
                                 "thread_id": tid, "content": "hi A"})
    assert _last(b, "message")["thread_id"] == tid
    assert "client_thread_id" not in _last(b, "message")
    # Both parties now list the conversation (the thread_id-only key dropped B's row).
    assert [t["thread_id"] for t in gw._store.get_dm_threads(owner_webid=bd)] == [tid]
    assert await _history(gw, a, tid) == ["hi B", "hi A"]


@pytest.mark.asyncio
async def test_local_dm_cannot_write_into_a_room(tmp_path):
    gw = _gateway(tmp_path)
    s = await _three_accounts(gw)
    (a, ad), (b, bd) = s["a"], s["b"]
    with sqlite3.connect(gw._store.db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO room_members (room_id, webid) VALUES (?, ?)",
                     ("room-x", bd))
    a.send.reset_mock()
    await gw.process_command(a, {"cmd": "local_dm", "target_webid": bd,
                                 "thread_id": "room-x", "content": "into the room?"})
    assert _last(a, "message")["thread_id"] != "room-x"
    assert gw._store.get_messages("room-x") == []


def test_migration_keeps_existing_dm_threads(tmp_path):
    """A pre-61 database (dm_threads keyed by thread_id) keeps every row and id."""
    db = str(tmp_path / "old.db")
    LocalStore(db)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE dm_threads")
        conn.execute("""CREATE TABLE dm_threads (
            thread_id TEXT PRIMARY KEY, peer_webid TEXT NOT NULL, display_name TEXT,
            owner_webid TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL)""")
        conn.executemany(
            "INSERT INTO dm_threads VALUES (?,?,?,?,?)",
            [("local-did-key-zB", "did:key:zB", "Bob", "did:key:zA", 1.0),
             ("cert-1", "did:key:zC", None, "did:key:zA", 2.0),
             ("legacy", "did:key:zD", "Dee", "", 3.0)],
        )
        conn.execute("UPDATE schema_version SET version = 60")

    s = LocalStore(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 61
    assert [(t["thread_id"], t["peer_webid"], t["display_name"])
            for t in s.get_dm_threads(owner_webid="did:key:zA")] == [
        ("local-did-key-zB", "did:key:zB", "Bob"), ("cert-1", "did:key:zC", None)]
    assert [t["thread_id"] for t in s.get_dm_threads(owner_webid="")] == ["legacy"]
    # The peer can now own its side of the same thread id.
    s.save_dm_thread("local-did-key-zB", "did:key:zA", "Al", owner_webid="did:key:zB")
    assert [t["thread_id"] for t in s.get_dm_threads(owner_webid="did:key:zB")] == ["local-did-key-zB"]
    assert s.get_dm_threads(owner_webid="did:key:zA")[0]["display_name"] == "Bob"


# ── /export is per account ──────────────────────────────────────────────────


def _two_account_store(path):
    s = LocalStore(path)
    s.save_dm_thread("t-ab", "did:key:zB", "Bob", owner_webid="did:key:zA")
    s.save_message("m-ab", "t-ab", "dm", "did:key:zA", "A", "A to B", "2024-01-01T00:00:00Z")
    s.save_dm_thread("t-cd", "did:key:zD", "Dee", owner_webid="did:key:zC")
    s.save_message("m-cd", "t-cd", "dm", "did:key:zC", "C", "C to D secret", "2024-01-01T00:00:01Z")
    s.save_display_name("did:key:zB", "Bob")
    s.save_display_name("did:key:zD", "Dee")
    return s


def test_export_scoped_to_account(tmp_path):
    s = _two_account_store(str(tmp_path / "s.db"))
    data = s.export_all(owner_webid="did:key:zA")
    assert [m["message_id"] for m in data["messages"]] == ["m-ab"]
    assert [t["thread_id"] for t in data["dm_threads"]] == ["t-ab"]
    assert "did:key:zD" not in {d["webid"] for d in data["display_names"]}
    # The peer's export includes the thread it was DMed in.
    assert [m["message_id"] for m in s.export_all(owner_webid="did:key:zB")["messages"]] == ["m-ab"]
    # Unscoped (operator) export is unchanged.
    assert {m["message_id"] for m in s.export_all()["messages"]} == {"m-ab", "m-cd"}


def test_import_keeps_owner_of_a_local_account(tmp_path):
    src = _two_account_store(str(tmp_path / "src.db"))
    data = src.export_all(owner_webid="did:key:zC")
    dst = LocalStore(str(tmp_path / "dst.db"))
    dst.save_dm_thread("t-other", "did:key:zX", None, owner_webid="did:key:zC")
    dst.import_data(data, owner_webid="did:key:zGateway")
    assert {t["thread_id"] for t in dst.get_dm_threads(owner_webid="did:key:zC")} == {"t-other", "t-cd"}
    assert dst.get_dm_threads(owner_webid="did:key:zGateway") == []
    # An owner that is not an account here is still re-stamped.
    data["dm_threads"][0]["owner_webid"] = "did:key:zStranger"
    data["dm_threads"][0]["thread_id"] = "t-new"
    dst.import_data(data, owner_webid="did:key:zGateway")
    assert [t["thread_id"] for t in dst.get_dm_threads(owner_webid="did:key:zGateway")] == ["t-new"]


def test_import_does_not_join_another_pairs_thread(tmp_path):
    dst = LocalStore(str(tmp_path / "dst.db"))
    dst.save_dm_thread("local-did-key-zB", "did:key:zB", None, owner_webid="did:key:zA")
    dst.import_data({"dm_threads": [{"thread_id": "local-did-key-zB", "peer_webid": "did:key:zB",
                                     "owner_webid": "did:key:zEve", "created_at": 1}]},
                    owner_webid="did:key:zEve")
    assert dst.get_dm_threads(owner_webid="did:key:zEve") == []


def _free_port():
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        return sk.getsockname()[1]


@pytest.mark.asyncio
async def test_http_export_ticket_scopes_to_the_socket_account(tmp_path):
    db = str(tmp_path / "gw.db")
    _two_account_store(db)
    ws_port, http_port = _free_port(), _free_port()
    gw = ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                             public_url=f"ws://127.0.0.1:{ws_port}", db_path=db),
    )
    start_gateway(gw, ws_port, http_port)

    ws = _ws()
    gw._client_webids[ws] = "did:key:zA"
    await gw._handle_export_ticket(ws)
    ticket = _last(ws, "export_ticket")["ticket"]

    # A fresh connection per request: the gateway closes after each response.
    base = f"http://127.0.0.1:{http_port}/export"
    r = httpx.get(f"{base}?ticket={ticket}", timeout=10)
    assert r.status_code == 200
    assert [m["message_id"] for m in r.json()["messages"]] == ["m-ab"]
    # Single use.
    r = httpx.get(f"{base}?ticket={ticket}", timeout=10)
    assert r.status_code == 403
    # Without a ticket, a gateway holding several accounts' data refuses.
    r = httpx.get(base, timeout=10)
    assert r.status_code == 403
    assert r.json()["error"] == "export_needs_account"
