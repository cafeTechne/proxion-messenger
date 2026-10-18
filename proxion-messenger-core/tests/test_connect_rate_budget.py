"""An account with many rooms connects and switches rooms within its budget.

With ~55+ rooms the web client's connect burst (a history fetch per room on
top of the init commands) exceeded the 60 commands / 10 s per-connection
budget, so get_rooms / get_dms were refused and the sidebars came up empty.
Snapshot reads now have their own budget, the room list carries unread counts
(so the client need not fetch every room's history), and writes keep the
60 / 10 s limit.
"""
import json
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState

N_ROOMS = 70
N_DMS = 5


def _did():
    return pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _ws():
    ws = AsyncMock()
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _sent(ws):
    return [json.loads(c[0][0]) for c in ws.send.call_args_list]


def _rate_errors(ws):
    return [m for m in _sent(ws) if m.get("code") == "E_RATE"]


def _last(ws, typ):
    for m in reversed(_sent(ws)):
        if m.get("type") == typ:
            return m
    return None


async def _register(gw, did, name="me"):
    ws = _ws()
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": name})
    return ws


@pytest.fixture
async def big_account(tmp_path):
    gw = ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "rooms.db")),
    )
    me = _did()
    setup = await _register(gw, me)
    for i in range(N_ROOMS):
        gw._rate_counters.clear()   # setup only: creating rooms is a write
        await gw.process_command(setup, {"cmd": "chat_room_create", "name": f"room {i}"})
    room_ids = [rid for rid, r in gw._local_rooms.items()]
    assert len(room_ids) == N_ROOMS
    peers = [_did() for _ in range(N_DMS)]
    for p in peers:
        gw._rate_counters.clear()
        await gw.process_command(setup, {"cmd": "local_dm", "target_webid": p,
                                         "content": "hi", "thread_id": "local-" + p[-20:]})
    # Another member posts 3 messages in room 0 while we are away.
    other = await _register(gw, _did(), "other")
    gw._store.add_room_member(room_ids[0], gw._client_webids[other])
    gw._local_rooms[room_ids[0]]["members"].add(other)
    for n in range(3):
        await gw.process_command(other, {"cmd": "send_room", "room_id": room_ids[0], "content": f"m{n}"})
    return gw, me, room_ids


@pytest.mark.asyncio
async def test_fresh_connect_with_many_rooms_gets_all_rooms_and_dms(big_account):
    gw, me, room_ids = big_account
    ws = await _register(gw, me)

    snap = _last(ws, "rooms")
    assert snap and len(snap["rooms"]) == N_ROOMS
    # Unread counts ride on the snapshot: no per-room history fetch needed.
    counts = {r["id"]: r["unread_count"] for r in snap["rooms"]}
    assert counts[room_ids[0]] == 3
    assert all(counts[r] == 0 for r in room_ids[1:])
    dms = _last(ws, "local_dms")
    assert dms and len(dms["dms"]) == N_DMS
    assert all("unread_count" in d for d in dms["dms"])

    # Worst case: an older client still fetches history for every room twice
    # (snapshot and get_rooms) plus every DM, around the init commands.
    for r in snap["rooms"]:
        await gw.process_command(ws, {"cmd": "get_local_history", "thread_id": r["id"], "limit": 100})
    for d in dms["dms"]:
        await gw.process_command(ws, {"cmd": "get_local_history", "thread_id": d["id"], "limit": 100})
    for cmd in ({"cmd": "set_receipts_enabled", "enabled": True},
                {"cmd": "set_link_previews_enabled", "enabled": False},
                {"cmd": "pod_status"},
                {"cmd": "get_peer_device_keys", "peer_webid": me}):
        await gw.process_command(ws, cmd)
    ws.send.reset_mock()
    for cmd in ("get_rooms", "get_dms", "get_identity", "list_friend_requests",
                "get_relationships", "list_blocks"):
        await gw.process_command(ws, {"cmd": cmd})
    rooms = _last(ws, "rooms")
    assert rooms is not None, _rate_errors(ws)
    assert len(rooms["rooms"]) == N_ROOMS
    assert {r["id"]: r["unread_count"] for r in rooms["rooms"]}[room_ids[0]] == 3
    assert _last(ws, "dms") is not None
    for r in rooms["rooms"]:
        await gw.process_command(ws, {"cmd": "get_local_history", "thread_id": r["id"], "limit": 100})
    assert _rate_errors(ws) == []


@pytest.mark.asyncio
async def test_rapid_room_switching_is_not_refused(big_account):
    gw, me, room_ids = big_account
    ws = await _register(gw, me)
    ws.send.reset_mock()
    # What opening a room sends, for 50 rooms in quick succession.
    for rid in room_ids[:50]:
        await gw.process_command(ws, {"cmd": "get_local_history", "thread_id": rid, "limit": 100})
        await gw.process_command(ws, {"cmd": "mark_read", "thread_id": rid})
        await gw.process_command(ws, {"cmd": "update_last_read", "channel_id": rid})
        await gw.process_command(ws, {"cmd": "get_room_members", "room_id": rid})
    assert _rate_errors(ws) == []
    hist = [m for m in _sent(ws) if m.get("type") == "local_history"]
    assert len(hist) == 50


@pytest.mark.asyncio
async def test_reads_do_not_spend_the_write_budget_and_writes_stay_limited(big_account):
    gw, me, room_ids = big_account
    ws = await _register(gw, me)
    # register spent one write; start the window clean so the count below does
    # not depend on how long the store took (the window is 10 s).
    gw._rate_counters.clear()
    for _ in range(200):
        await gw.process_command(ws, {"cmd": "get_rooms"})
    ws.send.reset_mock()
    for n in range(60):
        await gw.process_command(ws, {"cmd": "send_room", "room_id": room_ids[1], "content": f"x{n}"})
    assert _rate_errors(ws) == []
    await gw.process_command(ws, {"cmd": "send_room", "room_id": room_ids[1], "content": "one too many"})
    err = _rate_errors(ws)
    assert len(err) == 1 and err[0]["cmd"] == "send_room" and err[0]["retry_after"] >= 1


@pytest.mark.asyncio
async def test_snapshot_reads_have_a_ceiling(big_account):
    gw, me, room_ids = big_account
    ws = await _register(gw, me)
    for _ in range(gw._READ_RATE_LIMIT + 5):
        await gw.process_command(ws, {"cmd": "get_dms"})
    err = _rate_errors(ws)
    assert err and err[0]["cmd"] == "get_dms"
