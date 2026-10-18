"""An account's export, imported into a fresh gateway, brings everything back.

The export used to leave out rooms, pins, reactions, mutes, timers and voice
audio; the UI's download truncated messages over 4 KiB; and import stamped every
row with the gateway's DID instead of the account it belonged to.
"""
import json
import socket
import sqlite3
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gwharness import start_gateway
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.federation import Capability, RelationshipCertificate
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState

ACCOUNT = "did:key:zAccountAlice"
BOB = "did:key:zBob"
CAROL = "did:key:zCarol"
LONG = "L" * 6000
AUDIO = "QUJD" * 300


def _did():
    return pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _seed(store: LocalStore, gw_key) -> dict:
    """Give ACCOUNT one of each kind of data; return ids the test checks for."""
    peer = Ed25519PrivateKey.generate()
    cert = RelationshipCertificate(
        issuer=gw_key.public_key().public_bytes_raw().hex(),
        subject=peer.public_key().public_bytes_raw().hex(),
        capabilities=[Capability(with_="proxion://dm", can="dm/send")])
    cert.sign(gw_key)
    peer_did = pub_key_to_did(peer.public_key().public_bytes_raw())
    store.save_relationship(cert.to_dict(), peer_did=peer_did, owner_webid=ACCOUNT)

    store.save_dm_thread("dm-bob", BOB, "Bob", owner_webid=ACCOUNT)
    store.save_message("m-long", "dm-bob", "local_dm", ACCOUNT, "Alice", LONG,
                       "2026-10-01T00:00:00+00:00")
    store.save_message("m-pin", "dm-bob", "local_dm", BOB, "Bob", "pin me",
                       "2026-10-01T00:00:01+00:00")
    store.save_pin("dm-bob", "m-pin", ACCOUNT, "pin me")
    store.save_reaction("dm-bob", "m-pin", "+1", ACCOUNT)
    store.set_dm_disappear_timer("dm-bob", 60000)
    store.set_thread_mute(ACCOUNT, BOB, True)

    store.save_room("room-1", "Book club", "CODE1", "", "full", creator_webid=ACCOUNT)
    store.add_room_member("room-1", ACCOUNT)
    store.add_room_member("room-1", CAROL)
    store.set_room_role("room-1", CAROL, "moderator")
    store.ban_room_member("room-1", "did:key:zTroll", ACCOUNT, "spam")
    store.set_room_disappear_timer("room-1", 3600000)
    store.save_voice_message("m-voice", "room-1", "room", ACCOUNT, "Alice", AUDIO, 1234,
                             "2026-10-01T00:00:02+00:00")
    store.save_message("m-room", "room-1", "room", CAROL, "Carol", "hello room",
                       "2026-10-01T00:00:03+00:00")
    store.save_pin("room-1", "m-room", CAROL, "hello room")
    store.save_reaction("room-1", "m-room", "heart", CAROL)
    store.set_thread_mute(ACCOUNT, "room-1", True)

    store.set_block(ACCOUNT, "did:key:zBlocked", True)
    removed = Ed25519PrivateKey.generate()
    removed_did = pub_key_to_did(removed.public_key().public_bytes_raw())
    store.mark_revoked("cert-removed", removed_did)
    store.save_scheduled_message({"id": "s1", "thread_id": "dm-bob", "from_webid": ACCOUNT,
                                  "content": "later", "send_at": 9999999999, "created_at": 1})
    return {"peer_did": peer_did, "removed_did": removed_did}


def _assert_restored(dst: LocalStore, ids: dict) -> None:
    c = sqlite3.connect(dst.db_path)
    c.row_factory = sqlite3.Row
    one = lambda q, *a: c.execute(q, a).fetchone()  # noqa: E731

    assert one("SELECT owner_webid FROM dm_threads WHERE thread_id='dm-bob'")[0] == ACCOUNT
    rel = one("SELECT owner_webid FROM relationships WHERE peer_did=?", ids["peer_did"])
    assert rel is not None and rel[0] == ACCOUNT
    assert len(one("SELECT content FROM messages WHERE message_id='m-long'")[0]) == len(LONG)

    v = one("SELECT content_type, audio_b64, duration_ms FROM messages WHERE message_id='m-voice'")
    assert (v["content_type"], v["audio_b64"], v["duration_ms"]) == ("audio", AUDIO, 1234)

    room = one("SELECT * FROM rooms WHERE room_id='room-1'")
    assert room is not None
    assert (room["name"], room["code"], room["creator_webid"], room["history_mode"]) == (
        "Book club", "CODE1", ACCOUNT, "full")
    assert room["disappear_after_ms"] == 3600000
    assert set(dst.get_room_members("room-1")) == {ACCOUNT, CAROL}
    assert dst.get_room_role("room-1", CAROL) == "moderator"
    assert dst.is_room_banned("room-1", "did:key:zTroll")

    assert {p["message_id"] for p in dst.get_pins("dm-bob")} == {"m-pin"}
    assert {p["message_id"] for p in dst.get_pins("room-1")} == {"m-room"}
    assert {(r["message_id"], r["emoji"]) for r in dst.get_reactions("dm-bob")} == {("m-pin", "+1")}
    assert {(r["message_id"], r["emoji"]) for r in dst.get_reactions("room-1")} == {("m-room", "heart")}

    assert dst.get_dm_disappear_timer("dm-bob") == 60000
    assert set(dst.get_muted_keys(ACCOUNT)) == {BOB, "room-1"}
    assert dst.is_blocked_by(ACCOUNT, "did:key:zBlocked")
    assert ids["removed_did"] in dst.get_revoked_dids()
    assert [s["id"] for s in dst.get_scheduled_messages_for_user(ACCOUNT)] == ["s1"]


def test_store_round_trip_keeps_every_kind_of_data(tmp_path):
    gw_key = Ed25519PrivateKey.generate()
    src = LocalStore(str(tmp_path / "src.db"))
    ids = _seed(src, gw_key)
    exp = json.loads(json.dumps(src.export_all(minimize=False, owner_webid=ACCOUNT)))
    dst = LocalStore(str(tmp_path / "dst.db"))
    dst.import_data(exp, owner_pub_hex=gw_key.public_key().public_bytes_raw().hex(),
                    owner_webid=ACCOUNT)
    _assert_restored(dst, ids)


def test_import_cannot_add_members_to_an_existing_room(tmp_path):
    dst = LocalStore(str(tmp_path / "dst.db"))
    dst.save_room("room-1", "Mine", "MINE", "", "full", creator_webid="did:key:zOwner")
    dst.add_room_member("room-1", "did:key:zOwner")
    dst.import_data({
        "rooms": [{"room_id": "room-1", "name": "Hijack", "code": "HIJ", "creator_webid": ACCOUNT,
                   "history_mode": "full", "created_at": 1}],
        "room_members": [{"room_id": "room-1", "webid": ACCOUNT}],
        "room_roles": [{"room_id": "room-1", "webid": ACCOUNT, "role": "admin"}],
        "reactions": [{"room_id": "room-1", "message_id": "x", "emoji": "y",
                       "sender_webid": "did:key:zOwner"}],
    }, owner_webid=ACCOUNT)
    assert dst.get_room_members("room-1") == ["did:key:zOwner"]
    assert dst.get_room_role("room-1", ACCOUNT) != "admin"
    assert dst.get_reactions("room-1") == []
    assert dst.get_all_rooms()[0]["name"] == "Mine"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ws():
    ws = AsyncMock()
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _ticket(gw, webid, purpose=None):
    ws = _ws()
    gw._client_webids[ws] = webid
    data = {"cmd": "export_ticket"}
    if purpose:
        data["purpose"] = purpose
    import asyncio
    asyncio.run_coroutine_threadsafe(gw._handle_export_ticket(ws, data), gw_loop(gw)).result(5)
    for call in reversed(ws.send.call_args_list):
        m = json.loads(call[0][0])
        if m.get("type") == "export_ticket":
            return m["ticket"]
    raise AssertionError("no ticket")


_LOOPS = {}


def gw_loop(gw):
    return _LOOPS[id(gw)]


def _gateway(tmp_path, name, agent):
    ws_port, http_port = _free_port(), _free_port()
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                             public_url=f"ws://127.0.0.1:{ws_port}",
                             db_path=str(tmp_path / f"{name}.db")))
    h = start_gateway(gw, ws_port, http_port)
    _LOOPS[id(gw)] = h.loop
    return gw, http_port


def test_http_export_then_import_into_a_fresh_gateway(tmp_path):
    agent = AgentState.generate()
    gw_a, port_a = _gateway(tmp_path, "a", agent)
    ids = _seed(gw_a._store, agent.identity_key)

    # What the Export button does: a ticket from the account's socket, then GET.
    r = httpx.get(f"http://127.0.0.1:{port_a}/export?ticket={_ticket(gw_a, ACCOUNT)}", timeout=10)
    assert r.status_code == 200, r.text
    body = r.content
    assert json.loads(body)["minimized"] is False

    # Same gateway identity restored on a new machine, empty database.
    gw_b, port_b = _gateway(tmp_path, "b", agent)
    t = _ticket(gw_b, ACCOUNT, purpose="import")
    r = httpx.post(f"http://127.0.0.1:{port_b}/import?ticket={t}", content=body,
                   headers={"Content-Type": "application/json"}, timeout=10)
    assert r.status_code == 200, r.text
    _assert_restored(gw_b._store, ids)
    # The imported room is live without a restart.
    assert gw_b._local_rooms["room-1"]["name"] == "Book club"
    assert gw_b._room_codes.get("CODE1") == "room-1"

    # An import ticket is single use and cannot be used as an export ticket.
    r = httpx.post(f"http://127.0.0.1:{port_b}/import?ticket={t}", content=body,
                   headers={"Content-Type": "application/json"}, timeout=10)
    assert r.status_code == 403
    r = httpx.get(f"http://127.0.0.1:{port_b}/export?ticket={_ticket(gw_b, ACCOUNT, 'import')}",
                  timeout=10)
    assert r.status_code == 403
