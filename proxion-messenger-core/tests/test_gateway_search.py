"""Tests for Full-Text Search (FTS) backend."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState


@pytest.fixture
def agent():
    a = AgentState.generate()
    a.webid = "https://alice.pod/profile/card#me"
    return a


@pytest.fixture
def gateway_with_store(agent, tmp_path):
    cfg = GatewayConfig(db_path=str(tmp_path / "test.db"))
    return ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=cfg)


@pytest.fixture
def gateway_no_store(agent):
    return ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=GatewayConfig())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    return ws


async def _register(gw, ws, webid="https://alice.pod/profile/card#me"):
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    gw._display_names[ws] = "Alice"


def _setup_room(gw, ws, room_id="room-search", room_name="Search Room"):
    gw._local_rooms[room_id] = {
        "members": {ws},
        "messages": [],
        "history_mode": "none",
        "name": room_name,
        "code": "search-code",
    }


@pytest.mark.asyncio
async def test_search_returns_matching_messages(gateway_with_store):
    """FTS query returns messages with matching content."""
    ws = _mock_ws()
    await _register(gateway_with_store, ws)
    room_id = "room-search"
    _setup_room(gateway_with_store, ws, room_id, "Test Room")

    # Save a message to the store
    gateway_with_store._store.save_message(
        "msg-1", room_id, "room",
        "https://alice.pod/profile/card#me", "Alice",
        "hello world testing",
        "2026-04-30T12:00:00Z"
    )

    # Search for a matching term
    await gateway_with_store.process_command(ws, {
        "cmd": "search",
        "query": "hello",
    })

    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "search_results"
    assert resp["query"] == "hello"
    assert len(resp["results"]) >= 1
    assert resp["results"][0]["content"] == "hello world testing"
    assert resp["results"][0]["message_id"] == "msg-1"


@pytest.mark.asyncio
async def test_search_respects_thread_membership(gateway_with_store):
    """Results only from threads the user is in."""
    ws1 = _mock_ws()
    ws2 = _mock_ws()
    await _register(gateway_with_store, ws1, "https://alice.pod/profile/card#me")
    await _register(gateway_with_store, ws2, "https://bob.pod/profile/card#me")

    room_id_1 = "room-alice"
    room_id_2 = "room-bob"

    # Setup two rooms with different members
    _setup_room(gateway_with_store, ws1, room_id_1, "Alice's Room")
    _setup_room(gateway_with_store, ws2, room_id_2, "Bob's Room")

    # Save messages to both rooms
    gateway_with_store._store.save_message(
        "msg-alice", room_id_1, "room",
        "https://alice.pod/profile/card#me", "Alice",
        "secret message alice",
        "2026-04-30T12:00:00Z"
    )
    gateway_with_store._store.save_message(
        "msg-bob", room_id_2, "room",
        "https://bob.pod/profile/card#me", "Bob",
        "secret message bob",
        "2026-04-30T12:00:00Z"
    )

    # Search from ws1's perspective
    await gateway_with_store.process_command(ws1, {
        "cmd": "search",
        "query": "secret",
    })

    resp = json.loads(ws1.send.call_args[0][0])
    assert resp["type"] == "search_results"
    # ws1 should only see results from room_id_1
    assert len(resp["results"]) == 1
    assert resp["results"][0]["thread_id"] == room_id_1


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty(gateway_with_store):
    """Empty string query returns empty results, no error."""
    ws = _mock_ws()
    await _register(gateway_with_store, ws)
    room_id = "room-search"
    _setup_room(gateway_with_store, ws, room_id, "Test Room")

    # Save a message
    gateway_with_store._store.save_message(
        "msg-1", room_id, "room",
        "https://alice.pod/profile/card#me", "Alice",
        "hello world",
        "2026-04-30T12:00:00Z"
    )

    # Search with empty query
    await gateway_with_store.process_command(ws, {
        "cmd": "search",
        "query": "",
    })

    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "search_results"
    assert resp["query"] == ""
    assert resp["results"] == []


@pytest.mark.asyncio
async def test_search_result_includes_thread_name(gateway_with_store):
    """Result dict has thread_name field."""
    ws = _mock_ws()
    await _register(gateway_with_store, ws)
    room_id = "room-search"
    room_name = "My Test Room"
    _setup_room(gateway_with_store, ws, room_id, room_name)

    # Save a message
    gateway_with_store._store.save_message(
        "msg-1", room_id, "room",
        "https://alice.pod/profile/card#me", "Alice",
        "hello world",
        "2026-04-30T12:00:00Z"
    )

    # Search
    await gateway_with_store.process_command(ws, {
        "cmd": "search",
        "query": "hello",
    })

    resp = json.loads(ws.send.call_args[0][0])
    assert len(resp["results"]) >= 1
    result = resp["results"][0]
    assert result["thread_name"] == room_name


@pytest.mark.asyncio
async def test_search_memory_fallback(gateway_no_store):
    """In-memory scan works when store=None."""
    ws = _mock_ws()
    await _register(gateway_no_store, ws)
    room_id = "room-search"
    _setup_room(gateway_no_store, ws, room_id, "Test Room")

    # Add a message to the in-memory room messages list
    gateway_no_store._local_rooms[room_id]["messages"].append({
        "message_id": "msg-1",
        "content": "hello world testing",
        "from_webid": "https://alice.pod/profile/card#me",
        "from_display_name": "Alice",
        "timestamp": "2026-04-30T12:00:00Z",
    })

    # Search — should fall back to in-memory scan
    await gateway_no_store.process_command(ws, {
        "cmd": "search",
        "query": "hello",
    })

    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "search_results"
    assert resp["query"] == "hello"
    assert len(resp["results"]) >= 1
    assert resp["results"][0]["content"] == "hello world testing"


@pytest.mark.asyncio
async def test_search_fts_trigger_inserts(gateway_with_store):
    """FTS index populated via INSERT trigger (store-backed)."""
    ws = _mock_ws()
    await _register(gateway_with_store, ws)
    room_id = "room-search"
    _setup_room(gateway_with_store, ws, room_id, "Test Room")

    # Save a message — trigger should auto-index it
    gateway_with_store._store.save_message(
        "msg-fts-1", room_id, "room",
        "https://alice.pod/profile/card#me", "Alice",
        "this is a full text search test",
        "2026-04-30T12:00:00Z"
    )

    # Query the FTS index directly
    results = gateway_with_store._store.search_messages("full", [room_id])
    assert len(results) >= 1
    assert results[0]["message_id"] == "msg-fts-1"
    assert "full text search" in results[0]["content"]


@pytest.mark.asyncio
async def test_search_with_no_threads_does_not_search_everything(gateway_with_store):
    """A caller in no rooms and with no DMs must not get other threads' hits."""
    ws = _mock_ws()
    await _register(gateway_with_store, ws, "did:key:zLoner")
    gateway_with_store._store.save_message(
        "msg-other", "room-not-mine", "room", "did:key:zOther", "Other",
        "private planning notes", "2026-04-30T12:00:00Z")
    gateway_with_store._store.save_message(
        "dm-other", "dm-not-mine", "dm", "did:key:zOther", "Other",
        "private planning notes too", "2026-04-30T12:00:00Z")

    await gateway_with_store.process_command(ws, {"cmd": "search", "query": "private"})

    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "search_results"
    assert resp["results"] == []


@pytest.mark.asyncio
async def test_search_dm_scoped_to_own_account(gateway_with_store):
    """Plain (non-E2E) DM rows are searchable by their account only."""
    store = gateway_with_store._store
    alice, carol = "did:key:zAlice", "did:key:zCarol"
    store.save_dm_thread("dm-alice-bob", "did:key:zBob", owner_webid=alice)
    store.save_dm_thread("dm-carol-dan", "did:key:zDan", owner_webid=carol)
    store.save_message("m-ab", "dm-alice-bob", "dm", "did:key:zBob", "Bob",
                       "meet at the harbor", "2026-04-30T12:00:00Z")
    store.save_message("m-cd", "dm-carol-dan", "dm", "did:key:zDan", "Dan",
                       "meet at the station", "2026-04-30T12:00:00Z")

    ws_a, ws_c = _mock_ws(), _mock_ws()
    await _register(gateway_with_store, ws_a, alice)
    await _register(gateway_with_store, ws_c, carol)

    await gateway_with_store.process_command(ws_a, {"cmd": "search", "query": "meet"})
    got_a = [r["message_id"] for r in json.loads(ws_a.send.call_args[0][0])["results"]]
    await gateway_with_store.process_command(ws_c, {"cmd": "search", "query": "meet"})
    got_c = [r["message_id"] for r in json.loads(ws_c.send.call_args[0][0])["results"]]
    assert got_a == ["m-ab"]
    assert got_c == ["m-cd"]


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["e-mail", "v1.2", "don't", "AND", "x:y"])
async def test_search_query_punctuation_is_literal(gateway_with_store, query):
    ws = _mock_ws()
    await _register(gateway_with_store, ws)
    _setup_room(gateway_with_store, ws, "room-p", "Room P")
    gateway_with_store._store.save_message(
        "msg-p", "room-p", "room", "https://alice.pod/profile/card#me", "Alice",
        f"note: {query} here", "2026-04-30T12:00:00Z")

    await gateway_with_store.process_command(ws, {"cmd": "search", "query": query})

    resp = json.loads(ws.send.call_args[0][0])
    assert [r["message_id"] for r in resp["results"]] == ["msg-p"]


@pytest.mark.asyncio
async def test_search_does_not_return_edited_out_text(gateway_with_store):
    ws = _mock_ws()
    await _register(gateway_with_store, ws)
    _setup_room(gateway_with_store, ws, "room-e", "Room E")
    store = gateway_with_store._store
    store.save_message("msg-e", "room-e", "room", "https://alice.pod/profile/card#me",
                       "Alice", "my password is hunter2", "2026-04-30T12:00:00Z")
    store.update_message("msg-e", "oops, removed")

    await gateway_with_store.process_command(ws, {"cmd": "search", "query": "hunter2"})

    assert json.loads(ws.send.call_args[0][0])["results"] == []
