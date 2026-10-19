"""History paging, in-memory room history and store indexes stay bounded.

- A history page carried every reaction the thread ever had (419 KB per page
  on a busy room); it now carries the reactions for the page's own messages.
- A room with history_mode "all" appended every message to an in-memory list
  even with a store, which already serves join history and search.
- Hot lookups scanned whole tables or sorted in a temp b-tree.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


def _ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    return ws


def _sent(ws, type_):
    out = []
    for c in ws.send.call_args_list:
        m = json.loads(c[0][0])
        if m.get("type") == type_:
            out.append(m)
    return out


@pytest.fixture
def gw(tmp_path):
    g = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                       config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "h.db")),
                       read_state=ReadState())
    return g


def _seed(store, thread_id, n, kind="local_room"):
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    ids = []
    for i in range(n):
        mid = f"m{i:04d}"
        ts = (base + timedelta(seconds=i)).isoformat()
        store.save_message(mid, thread_id, kind, "did:key:zA", "A", f"msg {i}", ts)
        store.save_reaction(thread_id, mid, "+1", f"did:key:zR{i}")
        ids.append(mid)
    return ids


# ── perf7: reactions per page ─────────────────────────────────────────────

def test_store_get_reactions_filters_by_message_ids(tmp_path):
    s = LocalStore(str(tmp_path / "s.db"))
    _seed(s, "room-1", 5)
    assert len(s.get_reactions("room-1")) == 5
    got = s.get_reactions("room-1", ["m0001", "m0003", "nope"])
    assert {r["message_id"] for r in got} == {"m0001", "m0003"}
    assert set(got[0]) == {"message_id", "emoji", "sender_webid"}
    assert s.get_reactions("room-1", []) == []


@pytest.mark.asyncio
async def test_room_history_page_carries_only_its_reactions(gw):
    room_id = "room-busy"
    ws = _ws()
    gw._client_webids[ws] = "did:key:zA"
    gw._local_rooms[room_id] = {"name": "R", "members": {ws}, "history_mode": "none"}
    _seed(gw._store, room_id, 300)

    await gw.process_command(ws, {"cmd": "get_local_history", "thread_id": room_id, "limit": 50})
    page = _sent(ws, "local_history")[-1]
    page_ids = {m["message_id"] for m in page["messages"]}
    assert len(page_ids) == 50
    assert {r["message_id"] for r in page["reactions"]} == page_ids

    # The older page brings its own reactions.
    oldest = min(page["messages"], key=lambda m: m["timestamp"])["timestamp"]
    ws.send.reset_mock()
    await gw.process_command(ws, {"cmd": "get_local_history", "thread_id": room_id,
                                  "limit": 50, "before_timestamp": oldest})
    older = _sent(ws, "local_history")[-1]
    older_ids = {m["message_id"] for m in older["messages"]}
    assert older_ids and not (older_ids & page_ids)
    assert {r["message_id"] for r in older["reactions"]} == older_ids


@pytest.mark.asyncio
async def test_dm_history_page_carries_only_its_reactions(gw):
    me = "did:key:zA"
    cert_id = "11111111-2222-3333-4444-555555555555"
    _seed(gw._store, cert_id, 120, kind="dm")
    ws = _ws()
    gw._client_webids[ws] = me
    await gw._handle_read_dm(ws, {"cert_id": cert_id, "limit": 20})
    hist = _sent(ws, "history")[-1]
    ids = {m["message_id"] for m in hist["messages"]}
    assert len(ids) == 20
    assert {r["message_id"] for r in hist["reactions"]} == ids


# ── perf10: room["messages"] is not an unbounded mirror of the store ──────

@pytest.mark.asyncio
async def test_history_all_room_keeps_no_memory_copy_with_store(gw):
    room_id = "room-all"
    ws = _ws()
    gw.clients.add(ws)
    gw._client_webids[ws] = "did:key:zA"
    gw._display_names[ws] = "A"
    gw._local_rooms[room_id] = {"name": "R", "members": {ws}, "messages": [],
                                "history_mode": "all"}
    for i in range(5):
        await gw.process_command(ws, {"cmd": "send_room", "room_id": room_id, "content": f"m{i}"})
    assert len(gw._store.get_messages(room_id)) == 5
    assert gw._local_rooms[room_id]["messages"] == []


@pytest.mark.asyncio
async def test_history_all_room_memory_is_capped_without_store(gw, monkeypatch):
    import proxion_messenger_core._gateway_rooms as rooms_mod
    monkeypatch.setattr(rooms_mod, "_ROOM_MEMORY_CAP", 3)
    gw._store = None
    room_id = "room-mem"
    ws = _ws()
    gw.clients.add(ws)
    gw._client_webids[ws] = "did:key:zA"
    gw._display_names[ws] = "A"
    gw._local_rooms[room_id] = {"name": "R", "members": {ws}, "messages": [],
                                "history_mode": "all"}
    for i in range(6):
        await gw.process_command(ws, {"cmd": "send_room", "room_id": room_id, "content": f"m{i}"})
    kept = [m["content"] for m in gw._local_rooms[room_id]["messages"]]
    assert kept == ["m3", "m4", "m5"]


# ── perf14: indexes for the hot lookups ───────────────────────────────────

_PLANS = {
    "SELECT room_id FROM room_members WHERE webid = 'x'": "idx_room_members_webid",
    "SELECT * FROM dm_threads WHERE owner_webid = 'x' ORDER BY created_at ASC":
        "idx_dm_threads_owner_created",
    "SELECT * FROM webhooks WHERE thread_id = 'x' AND direction = 'in' AND active = 1":
        "idx_webhooks_thread_dir",
    "SELECT * FROM messages WHERE thread_id = 'x' ORDER BY timestamp DESC, received_at DESC LIMIT 100":
        "idx_msg_thread_ts_recv",
    "SELECT * FROM messages WHERE thread_id = 'x' AND timestamp > 'a' "
    "ORDER BY timestamp ASC, received_at ASC LIMIT 100": "idx_msg_thread_ts_recv",
}


def test_hot_queries_use_an_index_without_temp_sort(tmp_path):
    s = LocalStore(str(tmp_path / "i.db"))
    with s._conn() as c:
        for q, idx in _PLANS.items():
            plan = " | ".join(r[-1] for r in c.execute("EXPLAIN QUERY PLAN " + q))
            assert idx in plan, (q, plan)
            assert "TEMP B-TREE" not in plan, (q, plan)
            assert not plan.startswith("SCAN"), (q, plan)


def test_superseded_message_index_is_dropped_on_open(tmp_path):
    path = str(tmp_path / "old.db")
    LocalStore(path).close()
    raw = sqlite3.connect(path)
    raw.execute("CREATE INDEX IF NOT EXISTS idx_msg_thread_ts ON messages(thread_id, timestamp)")
    raw.commit()
    raw.close()
    LocalStore(path).close()
    raw = sqlite3.connect(path)
    names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    raw.close()
    assert "idx_msg_thread_ts_recv" in names
    assert "idx_msg_thread_ts" not in names
