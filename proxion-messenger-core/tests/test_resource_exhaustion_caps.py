"""Resource-exhaustion caps for client-driven write paths.

Each case is a disk-growth vector where a write bypassed a cap the primary path
enforces:

- B1: save_voice_message INSERTs into the same messages table as save_message
      but skipped the 5000/50MB per-thread quota.
- B2: edit_local_message content was bounded only by the WS frame (no schema
      cap), and every edit appended a message_edits row without limit.
- B4: _handle_rehost_room seeded a client-supplied members list with no cap,
      bypassing _MAX_ROOM_MEMBERS.
- B5: re-pinning the same DM message minted a new pins row each time.
"""
from __future__ import annotations

import base64
import json

import pytest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.command_validation import (
    validate_command_payload,
    SchemaError,
    _MAX_CONTENT,
)
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.room_descriptor import canonical_bytes
from proxion_messenger_core._gateway_rooms import (
    _MAX_ROOM_MEMBERS,
    _MAX_EDITS_PER_MESSAGE,
)


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "caps.db"))


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, o: self is o
    ws.remote_address = ("127.0.0.1", 1)
    return ws


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "gw.db")),
    )


# ---------------------------------------------------------------------------
# B1 — voice messages honour the per-thread quota
# ---------------------------------------------------------------------------

def _count(store, thread_id):
    with store._conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM messages WHERE thread_id = ?", (thread_id,)
        ).fetchone()[0]


def test_voice_message_refused_past_thread_count_cap(store):
    # Seed the thread up to the 5000-message ceiling.
    with store._conn() as conn:
        conn.executemany(
            "INSERT INTO messages (message_id, thread_id, thread_type, from_webid, content, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(f"m{i}", "capthread", "dm", "did:key:a", "x", "2026-01-01T00:00:00+00:00")
             for i in range(5000)],
        )
    assert _count(store, "capthread") == 5000

    store.save_voice_message(
        "v-over", "capthread", "dm", "did:key:a", "Alice",
        base64.b64encode(b"audio").decode(), 1000, "2026-01-01T00:00:01+00:00",
    )
    assert _count(store, "capthread") == 5000, "voice message must be refused over the count cap"


def test_normal_voice_message_still_stored(store):
    store.save_voice_message(
        "v-ok", "vthread", "dm", "did:key:a", "Alice",
        base64.b64encode(b"hello audio").decode(), 1500, "2026-01-01T00:00:00+00:00",
    )
    assert _count(store, "vthread") == 1
    msg = store.get_message("v-ok")
    assert msg["content_type"] == "audio"


# ---------------------------------------------------------------------------
# B2 (a) — edit_local_message content is schema-capped at 16 KB
# ---------------------------------------------------------------------------

def test_edit_local_message_content_over_cap_rejected():
    with pytest.raises(SchemaError):
        validate_command_payload(
            "edit_local_message",
            {"message_id": "m1", "content": "a" * (_MAX_CONTENT + 1)},
        )


def test_edit_local_message_content_at_cap_accepted():
    # At the limit must pass (no exception).
    validate_command_payload(
        "edit_local_message",
        {"message_id": "m1", "content": "a" * _MAX_CONTENT},
    )


# ---------------------------------------------------------------------------
# B2 (b) — message_edits rows are capped per message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_edit_history_capped_per_message(gateway):
    ws = _mock_ws()
    gateway._client_webids[ws] = "did:key:alice"
    gateway._local_rooms["room-e"] = {
        "name": "R", "code": "", "members": {ws},
        "creator_webid": "did:key:alice", "history_mode": "none", "messages": [],
    }
    gateway._store.save_message(
        "msg-e", "room-e", "local_room", "did:key:alice", "Alice",
        "original", "2026-01-01T00:00:00+00:00",
    )

    for i in range(_MAX_EDITS_PER_MESSAGE + 5):
        await gateway._handle_edit_local_message(
            ws, {"message_id": "msg-e", "thread_id": "room-e", "content": f"edit {i}"}
        )

    assert len(gateway._store.get_edits("msg-e")) == _MAX_EDITS_PER_MESSAGE, \
        "message_edits rows must not grow past the ceiling"
    last = json.loads(ws.send.call_args[0][0])
    assert last["type"] == "error" and last["message"] == "edit_history_full"


# ---------------------------------------------------------------------------
# B4 — rehost members list is capped at _MAX_ROOM_MEMBERS
# ---------------------------------------------------------------------------

def _signed_descriptor(priv, did, room_id, members):
    desc = {
        "px:type": "RoomDescriptor", "px:version": 1,
        "room_id": room_id, "title": "Room", "owner": did, "members": members,
    }
    sig = priv.sign(canonical_bytes(desc))
    return {**desc, "px:signer": did, "px:sig": base64.b64encode(sig).decode()}


@pytest.mark.asyncio
async def test_rehost_members_list_capped(gateway):
    gateway._force_auth = True
    room_id = "room-rehost-cap"
    priv = Ed25519PrivateKey.generate()
    did = pub_key_to_did(priv.public_key().public_bytes_raw())

    members = [{"webid": f"did:key:member{i}", "role": "member"} for i in range(600)]
    ws = _mock_ws()
    gateway._client_webids[ws] = "https://owner.example/card#me"
    gateway._session_signing_did[ws] = did

    await gateway._handle_rehost_room(
        ws, {"descriptor": _signed_descriptor(priv, did, room_id, members)}
    )

    stored = gateway._store.get_room_members(room_id)
    assert len(stored) == _MAX_ROOM_MEMBERS, \
        "rehost must cap the seeded members list at _MAX_ROOM_MEMBERS"


# ---------------------------------------------------------------------------
# B5 — re-pinning the same DM message does not duplicate rows
# ---------------------------------------------------------------------------

def test_repin_same_message_no_duplicate(store):
    pid1 = store.save_pin("thread-p", "msg-p", "did:key:a", "hello")
    pid2 = store.save_pin("thread-p", "msg-p", "did:key:b", "hello again")
    assert pid1 == pid2, "re-pin must reuse the existing pin id"
    pins = store.get_pins("thread-p")
    assert len(pins) == 1, "re-pinning must not mint a duplicate pins row"
