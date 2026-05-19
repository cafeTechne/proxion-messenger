"""Tests for pin_message / get_pins / unpin_message with prefixed thread_id."""
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
        config=GatewayConfig(port=9993, db_path=tmp_db),
        read_state=ReadState(),
    )


@pytest.fixture
def room_and_alice(gateway):
    """Set up a local room with alice as a connected member."""
    alice = MagicMock()
    alice.send = AsyncMock()
    bob = MagicMock()
    bob.send = AsyncMock()
    gateway.clients = {alice, bob}
    gateway._client_webids[alice] = "did:key:alice"
    gateway._client_webids[bob] = "did:key:bob"
    gateway._webid_sockets["did:key:alice"] = alice
    gateway._webid_sockets["did:key:bob"] = bob
    gateway._user_presence["did:key:alice"] = {"status": "online", "status_message": ""}
    gateway._user_presence["did:key:bob"] = {"status": "online", "status_message": ""}

    gateway._local_rooms["pin-room"] = {
        "name": "Pin Room", "code": "pincode", "members": {alice, bob},
        "invite_url": "", "history_mode": "none", "creator_webid": "did:key:alice",
        "messages": [],
    }
    gateway._room_codes["pincode"] = "pin-room"
    gateway._store.save_room("pin-room", "Pin Room", "pincode", "", "none")
    gateway._store.add_room_member("pin-room", "did:key:alice")
    gateway._store.add_room_member("pin-room", "did:key:bob")
    return alice, bob


@pytest.mark.asyncio
async def test_pin_message_with_prefixed_thread_id(gateway, room_and_alice):
    alice, _ = room_and_alice
    # Save a message so the gateway can pull its content
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    gateway._store.save_message("msg-pin-1", "pin-room", "room", "did:key:alice", "Alice", "hello pin", now)

    await gateway.process_command(alice, {
        "cmd": "pin_message",
        "message_id": "msg-pin-1",
        "thread_id": "room:pin-room",   # client sends prefixed ID
    })

    # Pin should be stored under the plain room_id
    pins = gateway._store.get_pins("pin-room")
    assert len(pins) == 1
    assert pins[0]["message_id"] == "msg-pin-1"


@pytest.mark.asyncio
async def test_get_pins_with_prefixed_thread_id(gateway, room_and_alice):
    alice, _ = room_and_alice
    # Store pin directly under plain id
    gateway._store.save_pin("pin-room", "msg-get-1", "did:key:alice", "content here")

    await gateway.process_command(alice, {
        "cmd": "get_pins",
        "thread_id": "room:pin-room",   # client sends prefixed ID
    })

    calls = alice.send.call_args_list
    payloads = [json.loads(c[0][0]) for c in calls]
    pins_event = next((p for p in payloads if p.get("type") == "pins"), None)
    assert pins_event is not None
    assert len(pins_event["pins"]) == 1
    assert pins_event["pins"][0]["message_id"] == "msg-get-1"


@pytest.mark.asyncio
async def test_unpin_broadcasts_to_members(gateway, room_and_alice):
    alice, bob = room_and_alice
    gateway._store.save_pin("pin-room", "msg-unpin-1", "did:key:alice", "text")

    await gateway.process_command(alice, {
        "cmd": "unpin_message",
        "message_id": "msg-unpin-1",
        "thread_id": "room:pin-room",
    })

    # Both members should receive the unpinned event
    for ws in (alice, bob):
        calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
        unpinned = [p for p in calls if p.get("type") == "unpinned"]
        assert len(unpinned) == 1
        assert unpinned[0]["message_id"] == "msg-unpin-1"


@pytest.mark.asyncio
async def test_pin_content_field_name(gateway, room_and_alice):
    alice, _ = room_and_alice
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    gateway._store.save_message("msg-cont-1", "pin-room", "room", "did:key:alice", "Alice", "content text", now)

    await gateway.process_command(alice, {"cmd": "pin_message", "message_id": "msg-cont-1", "thread_id": "room:pin-room"})
    await gateway.process_command(alice, {"cmd": "get_pins", "thread_id": "room:pin-room"})

    calls = [json.loads(c[0][0]) for c in alice.send.call_args_list]
    pins_event = next((p for p in calls if p.get("type") == "pins"), None)
    assert pins_event is not None
    pin = pins_event["pins"][0]
    assert "content" in pin           # must use 'content', not 'content_preview'
    assert pin["content"] == "content text"


@pytest.mark.asyncio
async def test_pin_pinned_by_field_name(gateway, room_and_alice):
    alice, _ = room_and_alice
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    gateway._store.save_message("msg-pby-1", "pin-room", "room", "did:key:alice", "Alice", "hi", now)

    await gateway.process_command(alice, {"cmd": "pin_message", "message_id": "msg-pby-1", "thread_id": "room:pin-room"})
    await gateway.process_command(alice, {"cmd": "get_pins", "thread_id": "room:pin-room"})

    calls = [json.loads(c[0][0]) for c in alice.send.call_args_list]
    pins_event = next((p for p in calls if p.get("type") == "pins"), None)
    assert pins_event is not None
    pin = pins_event["pins"][0]
    assert "pinned_by" in pin              # must use 'pinned_by', not 'pinned_by_webid'
    assert pin["pinned_by"] == "did:key:alice"
