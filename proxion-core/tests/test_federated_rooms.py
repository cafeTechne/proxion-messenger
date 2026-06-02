"""Tests: federated room member storage and relay."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


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
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "test.db"))
    return gw


def test_add_and_get_federated_room_member(store):
    """add_federated_room_member persists and get returns the member."""
    store.add_federated_room_member("room-1", "did:key:zBob", "https://bob.example.com")
    members = store.get_federated_room_members("room-1")
    assert len(members) == 1
    assert members[0]["member_did"] == "did:key:zBob"
    assert members[0]["gateway_url"] == "https://bob.example.com"


def test_remove_federated_room_member(store):
    """remove_federated_room_member removes a specific member."""
    store.add_federated_room_member("room-2", "did:key:zBob", "https://bob.example.com")
    store.add_federated_room_member("room-2", "did:key:zCarol", "https://carol.example.com")
    store.remove_federated_room_member("room-2", "did:key:zBob")
    members = store.get_federated_room_members("room-2")
    assert len(members) == 1
    assert members[0]["member_did"] == "did:key:zCarol"


def test_upsert_federated_member(store):
    """Adding the same member twice updates rather than duplicates."""
    store.add_federated_room_member("room-3", "did:key:zBob", "https://old.example.com")
    store.add_federated_room_member("room-3", "did:key:zBob", "https://new.example.com")
    members = store.get_federated_room_members("room-3")
    assert len(members) == 1
    assert members[0]["gateway_url"] == "https://new.example.com"


@pytest.mark.asyncio
async def test_announce_room_join_stores_federated_member(gateway):
    """announce_room_join records the caller's home gateway as a federated member."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    room_id = "room-fed-1"
    code = "testcode123"
    caller_webid = "did:key:zBob"
    gateway._client_webids[ws] = caller_webid
    gateway._local_rooms[room_id] = {
        "name": "Test Room", "code": code,
        "members": {ws}, "creator_webid": caller_webid,
    }
    gateway.clients.add(ws)

    await gateway._handle_announce_room_join(ws, {
        "room_id": room_id,
        "code": code,
        "home_gateway": "https://bob.example.com",
    })

    import json
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    # R30 T1: caller also receives room_member_joined; at minimum federated_room_joined is sent
    assert any(c.get("type") == "federated_room_joined" for c in calls)
    members = gateway._store.get_federated_room_members(room_id)
    assert any(m["member_did"] == caller_webid for m in members)


@pytest.mark.asyncio
async def test_room_relay_delivers_to_local_members(gateway):
    """_handle_room_relay delivers message to local members of the target room."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    room_id = "room-fed-2"
    gateway._local_rooms[room_id] = {"name": "Test", "members": {ws}}
    gateway.clients.add(ws)

    status, _ = await gateway._handle_room_relay({
        "room_id": room_id,
        "from_webid": "did:key:zAlice",
        "message_id": "msg-relay-1",
        "content": "Hello from Alice's gateway",
        "timestamp": "2026-05-24T00:00:00Z",
    })
    assert status.startswith("200")
    ws.send.assert_called_once()
    import json
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["content"] == "Hello from Alice's gateway"
