"""Room file attachments survive history: stored with the message, returned as
metadata in get_local_history, fetched with get_files (members only), and
removed with the message, the room, or disappearing expiry."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState

ROOM = "room-files"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
PNG_B64 = base64.b64encode(PNG).decode()


@pytest.fixture
def gw(tmp_path):
    g = ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9961, db_path=str(tmp_path / "f.db")),
        read_state=ReadState(),
    )
    g._store = LocalStore(str(tmp_path / "f.db"))
    g._local_rooms[ROOM] = {
        "name": "Files", "code": "c" * 64, "members": set(), "invite_url": "",
        "history_mode": "none", "messages": [], "creator_webid": "did:key:alice",
    }
    return g


def _sock(gw, webid, member=True):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    if member:
        gw._local_rooms[ROOM]["members"].add(ws)
    return ws


def _sent(ws, kind):
    out = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    return [e for e in out if e.get("type") == kind]


async def _send_png(gw, ws, **extra):
    await gw._handle_send_file(ws, {
        "room_id": ROOM, "filename": "pic.png", "mime_type": "image/png",
        "data_b64": PNG_B64, "width": 40, "height": 30, **extra,
    })
    msg = [e for e in _sent(ws, "message") if e.get("file")][-1]
    return msg["message_id"]


async def _history(gw, ws):
    ws.send.reset_mock()
    await gw._handle_get_local_history(ws, {"thread_id": ROOM})
    return _sent(ws, "local_history")[-1]["messages"]


async def _get_files(gw, ws, ids):
    ws.send.reset_mock()
    await gw._handle_get_files(ws, {"message_ids": ids})
    return _sent(ws, "file_data")


@pytest.mark.asyncio
async def test_history_carries_file_metadata_not_bytes(gw):
    alice = _sock(gw, "did:key:alice")
    mid = await _send_png(gw, alice)
    msgs = await _history(gw, alice)
    m = next(x for x in msgs if x["message_id"] == mid)
    assert m["file"]["filename"] == "pic.png"
    assert m["file"]["mime_type"] == "image/png"
    assert m["file"]["size"] == len(PNG)
    assert m["file"]["width"] == 40 and m["file"]["height"] == 30
    assert m["file"]["stored"] is True
    assert "data_b64" not in m["file"]


@pytest.mark.asyncio
async def test_member_fetches_bytes_including_late_joiner(gw):
    alice = _sock(gw, "did:key:alice")
    mid = await _send_png(gw, alice, spoiler=True)
    # Bob joins later: persistent membership, no live socket in the room yet.
    gw._store.add_room_member(ROOM, "did:key:bob")
    bob = _sock(gw, "did:key:bob", member=False)
    [ev] = await _get_files(gw, bob, [mid])
    assert ev["available"] is True
    assert base64.b64decode(ev["file"]["data_b64"]) == PNG
    assert ev["file"]["spoiler"] is True
    assert ev["thread_id"] == ROOM


@pytest.mark.asyncio
async def test_non_member_cannot_fetch(gw):
    alice = _sock(gw, "did:key:alice")
    mid = await _send_png(gw, alice)
    mallory = _sock(gw, "did:key:mallory", member=False)
    [ev] = await _get_files(gw, mallory, [mid])
    assert ev["available"] is False
    assert "file" not in ev
    # Same answer as an id that does not exist.
    [ev2] = await _get_files(gw, mallory, ["file-nope"])
    assert ev2 == {"type": "file_data", "message_id": "file-nope", "available": False}


@pytest.mark.asyncio
async def test_get_files_caps_batch(gw):
    alice = _sock(gw, "did:key:alice")
    evs = await _get_files(gw, alice, [f"file-{i}" for i in range(50)])
    assert len(evs) == gw.MAX_GET_FILES


@pytest.mark.asyncio
async def test_delete_message_removes_file(gw):
    alice = _sock(gw, "did:key:alice")
    mid = await _send_png(gw, alice)
    await gw._handle_delete_local_message(alice, {"message_id": mid, "thread_id": ROOM})
    assert gw._store.get_message_file(mid) is None
    [ev] = await _get_files(gw, alice, [mid])
    assert ev["available"] is False


@pytest.mark.asyncio
async def test_delete_room_removes_files(gw):
    alice = _sock(gw, "did:key:alice")
    mid = await _send_png(gw, alice)
    gw._store.delete_room(ROOM)
    assert gw._store.get_message_file(mid) is None


@pytest.mark.asyncio
async def test_disappearing_expiry_removes_files(gw):
    alice = _sock(gw, "did:key:alice")
    mid = await _send_png(gw, alice)
    cutoff = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    gw._store.delete_messages_before(ROOM, cutoff)
    assert gw._store.get_message_file(mid) is None


def test_store_refuses_oversized_and_orphan_files(tmp_path):
    s = LocalStore(str(tmp_path / "s.db"))
    ts = datetime.now(timezone.utc).isoformat()
    s.save_message("file-a", ROOM, "room", "did:key:a", "A", "📎 a.png", ts)
    assert s.save_message_file("file-a", ROOM, "a.png", "image/png",
                               b"x" * (s.MAX_MESSAGE_FILE_BYTES + 1)) is False
    # No message row in that thread: nothing to attach to.
    assert s.save_message_file("file-b", ROOM, "b.png", "image/png", b"x") is False
    assert s.save_message_file("file-a", "other-room", "a.png", "image/png", b"x") is False
    assert s.save_message_file("file-a", ROOM, "a.png", "image/png", b"x") is True


@pytest.mark.asyncio
async def test_legacy_file_row_is_marked_unavailable(gw):
    """A file message stored before files were kept (only "📎 name") must come
    back flagged, so the client shows an explicit unavailable state."""
    alice = _sock(gw, "did:key:alice")
    ts = datetime.now(timezone.utc).isoformat()
    gw._store.save_message("file-old", ROOM, "room", "did:key:alice", "A", "📎 old.pdf", ts)
    gw._store.save_message("msg-plain", ROOM, "room", "did:key:alice", "A", "📎 not a file", ts)
    msgs = {m["message_id"]: m for m in await _history(gw, alice)}
    assert msgs["file-old"]["file"] == {"filename": "old.pdf", "unavailable": True}
    assert "file" not in msgs["msg-plain"]
