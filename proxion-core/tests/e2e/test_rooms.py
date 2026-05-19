"""E2E tests for local room lifecycle: create, join, message, history, leave."""

import asyncio
import pytest

from proxion_messenger_core.persist import AgentState
from .helpers import connect_and_register


@pytest.mark.asyncio
async def test_room_create(alice_session):
    """Alice creates a room and gets room_created with room_id, name, code."""
    await alice_session.send(cmd="chat_room_create", name="Test Room")
    room = await alice_session.recv_type("room_created", timeout=5.0)

    assert room.get("name") == "Test Room"
    assert room.get("room_id", "").startswith("room-")
    assert room.get("code")


@pytest.mark.asyncio
async def test_room_join(live_gateway, alice_agent, bob_agent):
    """Bob joins Alice's room via invite code; both see membership events."""
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    bob = await connect_and_register(live_gateway["url"], "Bob", bob_agent)

    await alice.send(cmd="chat_room_create", name="Shared Room")
    room_evt = await alice.recv_type("room_created", timeout=5.0)
    code = room_evt["code"]
    room_id = room_evt["room_id"]

    # Bob joins by invite code
    await bob.send(cmd="join_room", code=code)
    joined = await bob.recv_type("room_joined", timeout=5.0)
    assert joined.get("room_id") == room_id

    # Alice should receive room_member_joined notification
    member_evt = await alice.recv_type("room_member_joined", timeout=5.0)
    assert member_evt.get("room_id") == room_id

    await alice.ws.close()
    await bob.ws.close()


@pytest.mark.asyncio
async def test_room_message(live_gateway, alice_agent, bob_agent):
    """Alice posts to a room; both Alice and Bob receive the message."""
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    bob = await connect_and_register(live_gateway["url"], "Bob", bob_agent)

    await alice.send(cmd="chat_room_create", name="Chat Room")
    room_evt = await alice.recv_type("room_created", timeout=5.0)
    code = room_evt["code"]
    room_id = room_evt["room_id"]

    await bob.send(cmd="join_room", code=code)
    await bob.recv_type("room_joined", timeout=5.0)
    await alice.recv_type("room_member_joined", timeout=5.0)

    # Alice sends a message to the room
    await alice.send(cmd="send_room", room_id=room_id, content="Hello room!")

    alice_msg = await alice.recv_type("message", timeout=5.0)
    assert alice_msg.get("content") == "Hello room!"

    bob_msg = await bob.recv_type("message", timeout=5.0)
    assert bob_msg.get("content") == "Hello room!"
    assert bob_msg.get("message_id") == alice_msg.get("message_id")

    await alice.ws.close()
    await bob.ws.close()


@pytest.mark.asyncio
async def test_room_history_on_join(live_gateway, alice_agent, bob_agent):
    """With history_mode='all', Bob receives prior messages when joining."""
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    bob = await connect_and_register(live_gateway["url"], "Bob", bob_agent)

    await alice.send(cmd="chat_room_create", name="History Room", history_mode="all")
    room_evt = await alice.recv_type("room_created", timeout=5.0)
    code = room_evt["code"]
    room_id = room_evt["room_id"]

    # Alice posts before Bob joins
    await alice.send(cmd="send_room", room_id=room_id, content="Pre-join message")
    await alice.recv_type("message", timeout=5.0)

    # Bob joins — should receive the historical message
    await bob.send(cmd="join_room", code=code)
    await bob.recv_type("room_joined", timeout=5.0)

    # Drain Bob's queue; history messages arrive right after room_joined
    events = await bob.drain(timeout=1.0)
    contents = [e.get("content") for e in events if e.get("type") == "message"]
    assert "Pre-join message" in contents

    await alice.ws.close()
    await bob.ws.close()


@pytest.mark.asyncio
async def test_room_get_members(live_gateway, alice_agent, bob_agent):
    """get_room_members returns both Alice and Bob after Bob joins."""
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    bob = await connect_and_register(live_gateway["url"], "Bob", bob_agent)

    await alice.send(cmd="chat_room_create", name="Members Room")
    room_evt = await alice.recv_type("room_created", timeout=5.0)
    code = room_evt["code"]
    room_id = room_evt["room_id"]

    await bob.send(cmd="join_room", code=code)
    await bob.recv_type("room_joined", timeout=5.0)
    await alice.recv_type("room_member_joined", timeout=5.0)

    await alice.send(cmd="get_room_members", room_id=room_id)
    members_evt = await alice.recv_type("room_members", timeout=5.0)
    webids = [m["webid"] for m in members_evt.get("members", [])]
    assert alice.did in webids
    assert bob.did in webids

    await alice.ws.close()
    await bob.ws.close()


@pytest.mark.asyncio
async def test_room_leave(live_gateway, alice_agent, bob_agent):
    """Bob can leave a room; Alice gets room_member_left."""
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    bob = await connect_and_register(live_gateway["url"], "Bob", bob_agent)

    await alice.send(cmd="chat_room_create", name="Leave Room")
    room_evt = await alice.recv_type("room_created", timeout=5.0)
    code = room_evt["code"]
    room_id = room_evt["room_id"]

    await bob.send(cmd="join_room", code=code)
    await bob.recv_type("room_joined", timeout=5.0)
    await alice.recv_type("room_member_joined", timeout=5.0)

    await bob.send(cmd="leave_local_room", room_id=room_id)
    left_evt = await bob.recv_type("left_room", timeout=5.0)
    assert left_evt.get("room_id") == room_id

    await alice.ws.close()
    await bob.ws.close()
