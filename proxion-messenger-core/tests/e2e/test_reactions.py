"""E2E tests for emoji reactions on DM and room messages."""

import asyncio
import pytest

from proxion_messenger_core.persist import AgentState
from .helpers import connect_and_register


@pytest.mark.asyncio
async def test_add_reaction_dm(alice_session, bob_session):
    """Alice sends a DM; Bob reacts; both get reaction_added."""
    bob_did = bob_session.did

    await alice_session.send(cmd="local_dm", target_webid=bob_did, content="React to this")
    alice_msg = await alice_session.recv_type("message", timeout=5.0)
    msg_id = alice_msg["message_id"]
    await bob_session.recv_type("message", timeout=5.0)

    # Bob reacts with a thumbs-up — cert_id is the DM thread_id (Bob's DID in Alice's thread)
    # From Bob's side, the thread_id is Alice's DID
    alice_did = alice_session.did
    await bob_session.send(cmd="add_reaction", cert_id=alice_did, message_id=msg_id, emoji="👍")

    bob_rx = await bob_session.recv_type("reaction_added", timeout=5.0)
    assert bob_rx.get("message_id") == msg_id
    assert bob_rx.get("emoji") == "👍"

    # Alice should also receive the reaction
    alice_rx = await alice_session.recv_type("reaction_added", timeout=5.0)
    assert alice_rx.get("message_id") == msg_id
    assert alice_rx.get("emoji") == "👍"


@pytest.mark.asyncio
async def test_remove_reaction_dm(alice_session, bob_session):
    """Bob adds then removes a reaction; both get reaction_removed."""
    bob_did = bob_session.did
    alice_did = alice_session.did

    await alice_session.send(cmd="local_dm", target_webid=bob_did, content="React and unreact")
    alice_msg = await alice_session.recv_type("message", timeout=5.0)
    msg_id = alice_msg["message_id"]
    await bob_session.recv_type("message", timeout=5.0)

    # Bob adds reaction
    await bob_session.send(cmd="add_reaction", cert_id=alice_did, message_id=msg_id, emoji="❤️")
    await bob_session.recv_type("reaction_added", timeout=5.0)
    await alice_session.recv_type("reaction_added", timeout=5.0)

    # Bob removes reaction
    await bob_session.send(cmd="remove_reaction", cert_id=alice_did, message_id=msg_id, emoji="❤️")
    bob_rm = await bob_session.recv_type("reaction_removed", timeout=5.0)
    assert bob_rm.get("message_id") == msg_id
    assert bob_rm.get("emoji") == "❤️"

    alice_rm = await alice_session.recv_type("reaction_removed", timeout=5.0)
    assert alice_rm.get("emoji") == "❤️"


@pytest.mark.asyncio
async def test_add_reaction_room(live_gateway, alice_agent, bob_agent):
    """Alice reacts to a room message; all members get reaction_added."""
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    bob = await connect_and_register(live_gateway["url"], "Bob", bob_agent)

    await alice.send(cmd="chat_room_create", name="Reaction Room")
    room_evt = await alice.recv_type("room_created", timeout=5.0)
    room_id = room_evt["room_id"]
    code = room_evt["code"]

    await bob.send(cmd="join_room", code=code)
    await bob.recv_type("room_joined", timeout=5.0)
    await alice.recv_type("room_member_joined", timeout=5.0)

    await bob.send(cmd="send_room", room_id=room_id, content="Room message")
    bob_msg = await bob.recv_type("message", timeout=5.0)
    msg_id = bob_msg["message_id"]
    await alice.recv_type("message", timeout=5.0)

    # Alice reacts to Bob's room message
    await alice.send(cmd="add_reaction", room_id=room_id, message_id=msg_id, emoji="🔥")

    alice_rx = await alice.recv_type("reaction_added", timeout=5.0)
    assert alice_rx.get("message_id") == msg_id
    assert alice_rx.get("emoji") == "🔥"

    bob_rx = await bob.recv_type("reaction_added", timeout=5.0)
    assert bob_rx.get("message_id") == msg_id
    assert bob_rx.get("emoji") == "🔥"

    await alice.ws.close()
    await bob.ws.close()
