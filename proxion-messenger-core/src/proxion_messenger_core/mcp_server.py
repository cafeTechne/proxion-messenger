"""MCP server exposing Proxion messaging to AI agents.

Lets an MCP-capable agent (Claude and others) participate in Proxion as a
first-class, cryptographically-signed identity: list and create rooms, read
history, and send room messages or direct messages. Every action travels
through the gateway over the same WebSocket protocol a human client uses
(see gateway_client.GatewayClient), so the agent is bound by the same consent,
contact and membership rules, with no privileged side door.

Run it (stdio transport, for Claude Desktop / Claude Code):

    pip install -e ".[gateway,mcp]"
    PROXION_GATEWAY_URL=ws://127.0.0.1:7474 proxion-mcp

Identity: set PROXION_AGENT_STATE (path to an agent.json, plus
PROXION_AGENT_PASSPHRASE if it is encrypted) to run as a persistent identity;
otherwise a fresh ephemeral identity is generated for the session. Configure
the agent's display name with PROXION_AGENT_NAME.

The MCP protocol owns stdout on the stdio transport, so every diagnostic here
goes to stderr.
"""

from __future__ import annotations

import asyncio
import os
import sys

from .persist import AgentState
from .gateway_client import GatewayClient, GatewayError


def _log(msg: str) -> None:
    print(f"[proxion-mcp] {msg}", file=sys.stderr, flush=True)


def _load_agent() -> AgentState:
    """Load the configured identity, or generate an ephemeral one."""
    path = os.environ.get("PROXION_AGENT_STATE")
    if path:
        passphrase = os.environ.get("PROXION_AGENT_PASSPHRASE")
        agent = AgentState.load(path, passphrase.encode() if passphrase else None)
        _log(f"loaded identity from {path}")
        return agent
    agent = AgentState.generate()
    _log("no PROXION_AGENT_STATE set; generated an ephemeral identity for this session")
    return agent


def build_server():
    """Construct the FastMCP server. Imported lazily so the rest of the package
    (and its tests) never hard-depend on the optional ``mcp`` SDK."""
    from mcp.server.fastmcp import FastMCP

    url = os.environ.get("PROXION_GATEWAY_URL", "ws://127.0.0.1:7474")
    name = os.environ.get("PROXION_AGENT_NAME", "Proxion Agent")
    agent = _load_agent()

    mcp = FastMCP("proxion-messenger")

    # One shared, lazily-connected client for the process lifetime.
    _state: dict = {"client": None}
    _connect_lock = asyncio.Lock()

    async def client() -> GatewayClient:
        async with _connect_lock:
            if _state["client"] is None:
                c = GatewayClient(url, agent, display_name=name)
                await c.connect()
                _log(f"connected to {url} as {c.did}")
                _state["client"] = c
            return _state["client"]

    @mcp.tool()
    async def whoami() -> dict:
        """Return this agent's Proxion identity (did:key, display name, WebID)."""
        return await (await client()).whoami()

    @mcp.tool()
    async def list_rooms() -> list:
        """List the rooms this agent is a member of."""
        return await (await client()).list_rooms()

    @mcp.tool()
    async def list_direct_messages() -> list:
        """List the agent's direct-message threads (peer WebIDs)."""
        return await (await client()).list_dms()

    @mcp.tool()
    async def create_room(name: str) -> dict:
        """Create a new room and return its id and join code."""
        return await (await client()).create_room(name)

    @mcp.tool()
    async def join_room(code: str) -> dict:
        """Join a room using an invite/join code."""
        return await (await client()).join_room(code)

    @mcp.tool()
    async def send_room_message(room_id: str, text: str) -> dict:
        """Send a message to a room the agent belongs to."""
        return await (await client()).send_room(room_id, text)

    @mcp.tool()
    async def send_direct_message(contact_did: str, text: str) -> dict:
        """Send a direct message to a contact's did:key. Subject to the gateway's
        contact/consent gating (a non-contact recipient triggers the normal
        friend-request flow rather than silent delivery)."""
        return await (await client()).send_dm(contact_did, text)

    @mcp.tool()
    async def read_history(thread_id: str, limit: int = 50) -> list:
        """Read recent messages from a room or DM thread (by room id or peer did)."""
        return await (await client()).read_history(thread_id, limit)

    @mcp.tool()
    async def search_messages(query: str) -> list:
        """Search the agent's accessible messages for a text query."""
        return await (await client()).search(query)

    @mcp.tool()
    async def edit_message(thread_id: str, message_id: str, text: str) -> dict:
        """Edit a message the agent previously sent, in a room or DM thread."""
        return await (await client()).edit_message(thread_id, message_id, text)

    @mcp.tool()
    async def delete_message(thread_id: str, message_id: str) -> dict:
        """Delete a message the agent previously sent, in a room or DM thread."""
        return await (await client()).delete_message(thread_id, message_id)

    @mcp.tool()
    async def react_to_message(thread_id: str, message_id: str, emoji: str) -> dict:
        """React to a message with an emoji, in a room or DM thread."""
        return await (await client()).react(thread_id, message_id, emoji)

    @mcp.tool()
    async def get_room_members(room_id: str) -> list:
        """List the members of a room the agent belongs to."""
        return await (await client()).get_room_members(room_id)

    return mcp


def main() -> None:
    try:
        server = build_server()
    except GatewayError as e:
        _log(f"startup failed: {e}")
        raise SystemExit(1)
    server.run()   # stdio transport


if __name__ == "__main__":
    main()
