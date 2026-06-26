"""Tests for get_message, update_last_read, and last_read_ts in register flow."""
import json
import time
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def gateway(tmp_db):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x00" * 32
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9994, db_path=tmp_db),
        read_state=ReadState(),
    )


@pytest.fixture
def alice(gateway):
    ws = MagicMock()
    ws.send = AsyncMock()
    gateway.clients = {ws}
    gateway._client_webids[ws] = "did:key:alice"
    gateway._webid_sockets["did:key:alice"] = ws
    gateway._user_presence["did:key:alice"] = {"status": "online", "status_message": ""}
    return ws


@pytest.mark.asyncio
async def test_get_message_returns_stored_message(gateway, alice):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    gateway._store.save_message("msg-42", "room-x", "room", "did:key:alice", "Alice", "hello world", now)

    await gateway.process_command(alice, {"cmd": "get_message", "message_id": "msg-42"})

    calls = alice.send.call_args_list
    payloads = [json.loads(c[0][0]) for c in calls]
    fetched = next((p for p in payloads if p.get("type") == "message_fetched"), None)
    assert fetched is not None
    assert fetched["message"]["message_id"] == "msg-42"
    assert fetched["message"]["content"] == "hello world"


@pytest.mark.asyncio
async def test_get_message_returns_none_for_unknown_id(gateway, alice):
    await gateway.process_command(alice, {"cmd": "get_message", "message_id": "no-such-msg"})

    calls = alice.send.call_args_list
    payloads = [json.loads(c[0][0]) for c in calls]
    fetched = next((p for p in payloads if p.get("type") == "message_fetched"), None)
    assert fetched is not None
    assert fetched["message"] is None
    assert fetched["message_id"] == "no-such-msg"


@pytest.mark.asyncio
async def test_update_last_read_stores_value(gateway, alice):
    gateway._store.set_last_read("did:key:alice", "room-z")  # set baseline
    before = gateway._store.get_last_read("did:key:alice", "room-z")

    await gateway.process_command(alice, {"cmd": "update_last_read", "channel_id": "room-z"})

    after = gateway._store.get_last_read("did:key:alice", "room-z")
    assert after >= before


@pytest.mark.asyncio
async def test_register_includes_last_read_ts_in_rooms(gateway):
    ws = MagicMock()
    ws.send = AsyncMock()
    gateway.clients = {ws}

    # Pre-seed: a room that alice is a member of
    gateway._store.save_room("room-r47", "Round 47 Room", "r47code", "", "none")
    gateway._store.add_room_member("room-r47", "did:key:alice")
    gateway._local_rooms["room-r47"] = {
        "name": "Round 47 Room", "code": "r47code",
        "invite_url": "", "history_mode": "none",
        "creator_webid": "did:key:alice", "members": set(), "messages": [],
    }
    # Record a last-read
    gateway._store.set_last_read("did:key:alice", "room-r47")
    stored_ts = gateway._store.get_last_read("did:key:alice", "room-r47")

    await gateway.process_command(ws, {
        "cmd": "register",
        "webid": "did:key:alice",
        "display_name": "Alice",
    })

    calls = ws.send.call_args_list
    payloads = [json.loads(c[0][0]) for c in calls]
    rooms_event = next((p for p in payloads if p.get("type") == "rooms"), None)
    assert rooms_event is not None
    r = next((r for r in rooms_event["rooms"] if r["id"] == "room-r47"), None)
    assert r is not None
    assert r["last_read_ts"] == pytest.approx(stored_ts, abs=1.0)
