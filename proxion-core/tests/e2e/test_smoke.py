"""E2E smoke tests for the Proxion gateway."""

import asyncio
import json
import pytest

from proxion_messenger_core.persist import AgentState
from .helpers import connect_and_register


@pytest.mark.asyncio
async def test_gateway_reachable(live_gateway):
    """Connect, receive config event, assert type=='config'."""
    import websockets

    ws = await websockets.connect(live_gateway["url"])
    config_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
    config = json.loads(config_raw)

    assert config.get("type") == "config"
    assert "first_run" in config

    await ws.close()


@pytest.mark.asyncio
async def test_register_two_users(live_gateway):
    """Alice and Bob both register, assert both get registered events."""
    alice_agent = AgentState.generate()
    bob_agent = AgentState.generate()

    alice_session = await connect_and_register(
        live_gateway["url"], "Alice", alice_agent
    )
    bob_session = await connect_and_register(
        live_gateway["url"], "Bob", bob_agent
    )

    # Both should have valid DIDs
    assert alice_session.did.startswith("did:key:")
    assert bob_session.did.startswith("did:key:")
    assert alice_session.did != bob_session.did

    await alice_session.ws.close()
    await bob_session.ws.close()


@pytest.mark.asyncio
async def test_direct_message_exchange(alice_session, bob_session):
    """Alice sends a direct message to Bob, Bob receives it."""
    bob_did = bob_session.did

    # Alice sends a DM to Bob
    await alice_session.send(
        cmd="local_dm",
        target_webid=bob_did,
        content="Hello Bob from Alice!",
    )

    # Alice should get her own message back
    alice_msg = await alice_session.recv_type("message", timeout=5.0)
    assert alice_msg.get("content") == "Hello Bob from Alice!"
    msg_id = alice_msg.get("message_id")

    # Bob should receive the message
    bob_msg = await bob_session.recv_type("message", timeout=5.0)
    assert bob_msg.get("content") == "Hello Bob from Alice!"
    assert bob_msg.get("message_id") == msg_id


@pytest.mark.asyncio
async def test_message_exchange(alice_session, bob_session):
    """Alice+Bob exchange messages, both sides receive."""
    bob_did = bob_session.did
    alice_did = alice_session.did

    # Alice sends a message to Bob
    await alice_session.send(
        cmd="local_dm",
        target_webid=bob_did,
        content="Hello, Bob!",
    )

    # Alice should get her own message back
    alice_msg = await alice_session.recv_type("message", timeout=5.0)
    assert alice_msg.get("content") == "Hello, Bob!"
    msg_id = alice_msg.get("message_id")

    # Bob should receive the message
    bob_msg = await bob_session.recv_type("message", timeout=5.0)
    assert bob_msg.get("content") == "Hello, Bob!"
    assert bob_msg.get("message_id") == msg_id

    # Bob responds
    await bob_session.send(
        cmd="local_dm",
        target_webid=alice_did,
        content="Hi Alice!",
    )

    # Both should get Bob's response
    alice_reply = await alice_session.recv_type("message", timeout=5.0)
    assert alice_reply.get("content") == "Hi Alice!"

    bob_reply = await bob_session.recv_type("message", timeout=5.0)
    assert bob_reply.get("content") == "Hi Alice!"


@pytest.mark.asyncio
async def test_reply_threading(alice_session, bob_session):
    """Bob replies with reply_to_id, Alice gets reply_to_id in event."""
    bob_did = bob_session.did
    alice_did = alice_session.did

    # Alice sends message
    await alice_session.send(
        cmd="local_dm",
        target_webid=bob_did,
        content="What's up?",
    )
    alice_msg = await alice_session.recv_type("message", timeout=5.0)
    msg_id = alice_msg.get("message_id")

    bob_msg = await bob_session.recv_type("message", timeout=5.0)
    assert bob_msg.get("message_id") == msg_id

    # Bob replies with reply_to_id
    await bob_session.send(
        cmd="local_dm",
        target_webid=alice_did,
        content="Not much!",
        reply_to_id=msg_id,
    )

    bob_reply = await bob_session.recv_type("message", timeout=5.0)
    assert bob_reply.get("reply_to_id") == msg_id

    # Alice should see the reply_to_id
    alice_reply = await alice_session.recv_type("message", timeout=5.0)
    assert alice_reply.get("reply_to_id") == msg_id


@pytest.mark.asyncio
async def test_presence_exchange(alice_session, bob_session):
    """Both users can set and get presence."""
    # Drain any existing presence updates from registration
    try:
        await alice_session.drain(timeout=0.5)
        await bob_session.drain(timeout=0.5)
    except Exception:
        pass

    # Alice sets presence
    await alice_session.send(
        cmd="set_presence",
        status="away",
    )

    # Alice should get a presence_update event
    alice_presence = await alice_session.recv_type("presence_update", timeout=5.0)
    assert alice_presence.get("status") == "away"

    # Bob should get Alice's presence update
    bob_presence = await bob_session.recv_type("presence_update", timeout=5.0)
    assert bob_presence.get("status") == "away"


@pytest.mark.asyncio
async def test_message_delete(alice_session, bob_session):
    """Alice deletes her message, both get message_deleted."""
    bob_did = bob_session.did
    alice_did = alice_session.did

    # Alice sends message
    await alice_session.send(
        cmd="local_dm",
        target_webid=bob_did,
        content="This will be deleted",
    )
    alice_msg = await alice_session.recv_type("message", timeout=5.0)
    msg_id = alice_msg.get("message_id")

    await bob_session.recv_type("message", timeout=5.0)

    # Alice deletes the message
    await alice_session.send(
        cmd="delete_local_message",
        thread_id=bob_did,
        message_id=msg_id,
    )

    # Both should get message_deleted
    alice_del = await alice_session.recv_type("message_deleted", timeout=5.0)
    assert alice_del.get("message_id") == msg_id

    bob_del = await bob_session.recv_type("message_deleted", timeout=5.0)
    assert bob_del.get("message_id") == msg_id


@pytest.mark.asyncio
async def test_multiple_messages_in_thread(alice_session, bob_session):
    """Send multiple messages in one DM thread."""
    bob_did = bob_session.did

    # Alice sends multiple messages
    for i in range(3):
        await alice_session.send(
            cmd="local_dm",
            target_webid=bob_did,
            content=f"Message {i+1}",
        )
        alice_msg = await alice_session.recv_type("message", timeout=5.0)
        assert alice_msg.get("content") == f"Message {i+1}"

        bob_msg = await bob_session.recv_type("message", timeout=5.0)
        assert bob_msg.get("content") == f"Message {i+1}"


@pytest.mark.asyncio
async def test_resolve_did(alice_session):
    """Resolve a DID and get the corresponding webid."""
    from proxion_messenger_core.didkey import agent_did

    alice_did = alice_session.did

    # Request resolution of Alice's DID
    await alice_session.send(cmd="resolve_did", did=alice_did)

    # Get the resolution response
    resolved = await alice_session.recv_type("did_resolved", timeout=5.0)
    assert resolved.get("did") == alice_did
