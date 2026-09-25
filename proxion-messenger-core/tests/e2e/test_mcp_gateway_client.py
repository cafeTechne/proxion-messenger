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
