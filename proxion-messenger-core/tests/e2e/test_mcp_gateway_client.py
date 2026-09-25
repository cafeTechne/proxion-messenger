"""E2E tests for the MCP server's transport (GatewayClient).

These drive the shippable GatewayClient against a live gateway, exercising the
exact handshake + commands the MCP tools call. The MCP tool layer itself is a
thin async wrapper over these methods, so verifying the client verifies the
server's behaviour without standing up an MCP stdio session.
"""

import pytest

from proxion_messenger_core.gateway_client import GatewayClient


@pytest.mark.asyncio
async def test_client_registers_and_reports_identity(live_gateway, alice_agent):
    client = GatewayClient(live_gateway["url"], alice_agent, "Agent A")
    await client.connect()
    try:
        who = await client.whoami()
        assert who["did"].startswith("did:key:")
        assert who["did"] == client.did
        assert who["display_name"] == "Agent A"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_client_creates_lists_and_posts_to_a_room(live_gateway, alice_agent):
    client = GatewayClient(live_gateway["url"], alice_agent, "Agent A")
    await client.connect()
    try:
        room = await client.create_room("Agent Room")
        room_id = room["room_id"]
        assert room_id

        # The created room shows up in the agent's own room list. This also
        # guards the buffering fix: the fresh get_rooms response must win over
        # the room snapshot the gateway pushes at registration time.
        rooms = await client.list_rooms()
        assert any(r.get("id") == room_id for r in rooms)

        # Posting returns the echoed message.
        sent = await client.send_room(room_id, "hello from an agent")
        assert sent["content"] == "hello from an agent"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_client_direct_message_and_history(live_gateway, alice_agent, bob_agent):
    alice = GatewayClient(live_gateway["url"], alice_agent, "Agent A")
    bob = GatewayClient(live_gateway["url"], bob_agent, "Agent B")
    await alice.connect()
    await bob.connect()
    try:
        await alice.send_dm(bob.did, "hi bob")
        # The gateway keys a local DM thread by the peer's did:key; Alice's own
        # view of that thread includes the message she just sent.
        history = await alice.read_history(bob.did, limit=10)
        assert any("hi bob" in m.get("content", "") for m in history)
    finally:
        await alice.close()
        await bob.close()


@pytest.mark.asyncio
async def test_client_edits_and_deletes_a_dm(live_gateway, alice_agent, bob_agent):
    alice = GatewayClient(live_gateway["url"], alice_agent, "Agent A")
    bob = GatewayClient(live_gateway["url"], bob_agent, "Agent B")
    await alice.connect()
    await bob.connect()
    try:
        sent = await alice.send_dm(bob.did, "original text")
        message_id = sent["message_id"]

        edited = await alice.edit_message(bob.did, message_id, "corrected text")
        assert edited["message_id"] == message_id
        assert edited["content"] == "corrected text"

        deleted = await alice.delete_message(bob.did, message_id)
        assert deleted["message_id"] == message_id
    finally:
        await alice.close()
        await bob.close()


@pytest.mark.asyncio
async def test_client_reacts_to_a_room_message(live_gateway, alice_agent, bob_agent):
    alice = GatewayClient(live_gateway["url"], alice_agent, "Agent A")
    bob = GatewayClient(live_gateway["url"], bob_agent, "Agent B")
    await alice.connect()
    await bob.connect()
    try:
        room = await alice.create_room("Agent Reaction Room")
        room_id = room["room_id"]
        sent = await alice.send_room(room_id, "react to this")
        message_id = sent["message_id"]

        reacted = await alice.react(room_id, message_id, "\U0001F44D")
        assert reacted["message_id"] == message_id
        assert reacted["emoji"] == "\U0001F44D"

        members = await alice.get_room_members(room_id)
        webids = [m.get("webid") for m in members]
        assert alice.did in webids
    finally:
        await alice.close()
        await bob.close()
