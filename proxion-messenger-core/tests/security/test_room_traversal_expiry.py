"""R120 WS-B: pod path traversal on room delete/edit, mark_read authz, and
delete completeness."""
from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core._gateway_rooms import _is_safe_message_id


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    agent.identity_key.private_bytes = MagicMock(return_value=b"\x42" * 32)
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9992, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "test.db"))
    return gw


def _ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    return ws


# ── F2: message-id charset guard ──────────────────────────────────────────────

def test_is_safe_message_id_rejects_traversal():
    assert _is_safe_message_id("local-abc123") is True
    assert _is_safe_message_id("11111111-2222-3333-4444-555555555555") is True
    for bad in ("../room", "../../relationships/x", "a/b", ".", "..", "", None, "x" * 200):
        assert _is_safe_message_id(bad) is False


@pytest.mark.asyncio
async def test_delete_local_message_rejects_traversal_id(gateway):
    ws = _ws()
    gateway._client_webids[ws] = "did:key:zAlice"
    gateway._local_rooms["room-x"] = {"name": "T", "code": "c",
                                      "members": {ws}, "creator_webid": "did:key:zAlice"}
    pod_deletes = []
    gateway._delete_room_message_on_pod = AsyncMock(side_effect=lambda r, m: pod_deletes.append(m))

    await gateway._handle_delete_local_message(
        ws, {"thread_id": "room-x", "message_id": "../../rooms/victim/room"})

    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("message") == "Invalid message id"
    assert pod_deletes == []   # never reached a pod delete


@pytest.mark.asyncio
async def test_edit_local_message_rejects_traversal_id(gateway):
    ws = _ws()
    gateway._client_webids[ws] = "did:key:zAlice"
    gateway._local_rooms["room-y"] = {"name": "T", "code": "c",
                                      "members": {ws}, "creator_webid": "did:key:zAlice"}
    await gateway._handle_edit_local_message(
        ws, {"thread_id": "room-y", "message_id": "../room", "content": "x"})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("message") == "Invalid message id"


@pytest.mark.asyncio
async def test_delete_unknown_id_does_not_reach_pod(gateway):
    # A well-formed id with no owning row must not trigger a pod delete (the
    # ownership-bypass fix): only a row the caller owns touches the pod.
    ws = _ws()
    gateway._client_webids[ws] = "did:key:zAlice"
    gateway._local_rooms["room-z"] = {"name": "T", "code": "c",
                                      "members": {ws}, "creator_webid": "did:key:zAlice"}
    gateway._delete_room_message_on_pod = AsyncMock()
    await gateway._handle_delete_local_message(
        ws, {"thread_id": "room-z", "message_id": "local-nonexistent"})
    gateway._delete_room_message_on_pod.assert_not_called()


# ── F6: mark_read must not act on a thread the caller cannot read ─────────────

@pytest.mark.asyncio
async def test_mark_read_rejected_for_non_participant(gateway):
    gateway._force_auth = True
    ws = _ws()
    gateway._client_webids[ws] = "did:key:zStranger"
    # A room the stranger is not a member of.
    gateway._local_rooms["room-secret"] = {"name": "T", "code": "c",
                                           "members": set(), "creator_webid": "did:key:zOwner"}
    await gateway._handle_mark_read(
        ws, {"thread_id": "room-secret", "message_id": "local-m1"})
    # No receipt written for the stranger.
    assert gateway._store.get_message_readers("local-m1") == []


# ── F11: delete_message purges dependent content rows ────────────────────────

def test_delete_message_purges_pins_edits_receipts(store):
    mid = "local-purge1"
    store.save_message(mid, "room-p", "room", "did:key:zA", "A", "hi",
                       "2026-01-01T00:00:00Z")
    store.save_pin("room-p", mid, "did:key:zA", content="hi")
    store.save_edit("edit-1", mid, "hi", "hello", "did:key:zA", "2026-01-01T00:01:00Z")
    store.save_message_receipt(mid, "did:key:zB", "2026-01-01T00:02:00Z")
    assert store.get_pins("room-p") and store.get_edits(mid) and store.get_message_readers(mid)

    store.delete_message(mid)

    assert store.get_message(mid) is None
    assert store.get_pins("room-p") == []
    assert store.get_edits(mid) == []
    assert store.get_message_readers(mid) == []
