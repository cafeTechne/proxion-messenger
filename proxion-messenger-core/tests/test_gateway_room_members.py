"""Tests for get_room_members and leave_local_room commands."""
import json
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
        config=GatewayConfig(port=9995, db_path=tmp_db),
        read_state=ReadState(),
    )


@pytest.fixture
def two_clients(gateway):
    alice = MagicMock()
    alice.send = AsyncMock()
    bob = MagicMock()
    bob.send = AsyncMock()
    gateway.clients = {alice, bob}
    gateway._client_webids[alice] = "did:key:alice"
    gateway._client_webids[bob] = "did:key:bob"
    gateway._webid_sockets["did:key:alice"] = alice
    gateway._webid_sockets["did:key:bob"] = bob
    gateway._display_names[alice] = "Alice"
    gateway._display_names[bob] = "Bob"
    gateway._user_presence["did:key:alice"] = {"status": "online"}
    gateway._user_presence["did:key:bob"] = {"status": "away"}
    return alice, bob


@pytest.mark.asyncio
async def test_get_room_members_returns_list(gateway, two_clients):
    alice, bob = two_clients
    gateway._local_rooms["room-members"] = {
        "name": "Test", "code": "aaa", "members": {alice, bob},
        "invite_url": "", "history_mode": "none", "messages": [],
    }
    await gateway.process_command(alice, {
        "cmd": "get_room_members",
        "room_id": "room-members",
    })
    calls = alice.send.call_args_list
    payloads = [json.loads(c[0][0]) for c in calls]
    room_members_events = [p for p in payloads if p.get("type") == "room_members"]
    assert len(room_members_events) == 1
    members = room_members_events[0]["members"]
    webids = {m["webid"] for m in members}
    assert "did:key:alice" in webids
    assert "did:key:bob" in webids


@pytest.mark.asyncio
async def test_get_room_members_includes_presence(gateway, two_clients):
    alice, bob = two_clients
    gateway._local_rooms["room-presence"] = {
        "name": "Test", "code": "bbb", "members": {alice, bob},
        "invite_url": "", "history_mode": "none", "messages": [],
    }
    await gateway.process_command(alice, {
        "cmd": "get_room_members",
        "room_id": "room-presence",
    })
    calls = alice.send.call_args_list
    payloads = [json.loads(c[0][0]) for c in calls]
    members = next(p["members"] for p in payloads if p.get("type") == "room_members")
    by_webid = {m["webid"]: m for m in members}
    assert by_webid["did:key:alice"]["status"] == "online"
    assert by_webid["did:key:bob"]["status"] == "away"


@pytest.mark.asyncio
async def test_leave_local_room_removes_member(gateway, two_clients):
    alice, bob = two_clients
    gateway._local_rooms["room-leave"] = {
        "name": "LeaveTest", "code": "ccc", "members": {alice, bob},
        "invite_url": "", "history_mode": "none", "messages": [],
    }
    gateway._room_codes["ccc"] = "room-leave"
    gateway._store.save_room("room-leave", "LeaveTest", "ccc", "", "none")
    gateway._store.add_room_member("room-leave", "did:key:alice")

    await gateway.process_command(alice, {
        "cmd": "leave_local_room",
        "room_id": "room-leave",
    })
    # Alice should be removed from in-memory set
    assert alice not in gateway._local_rooms["room-leave"]["members"]
    # Alice should be removed from DB
    db_members = gateway._store.get_room_members("room-leave")
    assert "did:key:alice" not in db_members

    # left_room event sent to alice
    calls = alice.send.call_args_list
    payloads = [json.loads(c[0][0]) for c in calls]
    left_events = [p for p in payloads if p.get("type") == "left_room"]
    assert len(left_events) == 1
    assert left_events[0]["room_id"] == "room-leave"


@pytest.mark.asyncio
async def test_leave_nonexistent_room_is_safe(gateway, two_clients):
    alice, _ = two_clients
    await gateway.process_command(alice, {
        "cmd": "leave_local_room",
        "room_id": "no-such-room",
    })
    calls = alice.send.call_args_list
    payloads = [json.loads(c[0][0]) for c in calls]
    left_events = [p for p in payloads if p.get("type") == "left_room"]
    assert len(left_events) == 1
    assert left_events[0]["room_id"] == "no-such-room"
