"""Integration tests for Proxion Gateway."""
from __future__ import annotations

import asyncio
import json
import pytest
import websockets
from proxion_messenger_core import AgentState
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig


async def recv_skip_config(ws):
    """Receive from ws, skipping any initial 'config' or 'registered' events."""
    for _ in range(5):
        raw = await ws.recv()
        msg = json.loads(raw)
        if msg.get("type") not in ("config", "registered"):
            return msg
    return json.loads(raw)


async def _register(ws, webid="did:key:integrationtestuser"):
    """Register a live WebSocket client and wait for confirmation."""
    await ws.send(json.dumps({"cmd": "register", "webid": webid}))
    for _ in range(10):
        raw = await asyncio.wait_for(ws.recv(), timeout=3)
        if json.loads(raw).get("type") == "registered":
            return

@pytest.fixture
async def gateway_server():
    """Fixture that starts a gateway on a random free port."""
    agent = AgentState.generate()
    # port=0 tells OS to pick a random free port
    config = GatewayConfig(host="127.0.0.1", port=0, poll_interval=100.0)
    gw = ProxionGateway(agent, {}, {}, config=config)
    
    # Manually start websockets server using the gateway's handler
    server = await websockets.serve(gw.handle_client, config.host, config.port)
    # Extract the port assigned by the OS
    actual_port = server.sockets[0].getsockname()[1]
    url = f"ws://127.0.0.1:{actual_port}"
    
    # Note: We don't start the poll_loop here as most command tests 
    # don't require external Pod polling.
    
    yield url, gw
    
    server.close()
    await server.wait_closed()

@pytest.mark.asyncio
async def test_gateway_get_identity(gateway_server):
    """Verify that get_identity returns the correct WebID."""
    url, gw = gateway_server
    async with websockets.connect(url) as ws:
        await _register(ws)
        await ws.send(json.dumps({"cmd": "get_identity"}))
        resp = await recv_skip_config(ws)
        assert resp["type"] == "identity"
        assert resp["webid"] == gw.agent.identity_pub_bytes.hex()

@pytest.mark.asyncio
async def test_gateway_unknown_command(gateway_server):
    """Verify that an unknown command returns an error."""
    url, _ = gateway_server
    async with websockets.connect(url) as ws:
        await _register(ws)
        await ws.send(json.dumps({"cmd": "not_a_command"}))
        resp = await recv_skip_config(ws)
        assert resp["type"] == "error"
        assert "Unknown command" in resp["message"]

@pytest.mark.asyncio
async def test_gateway_typing_relay(gateway_server):
    """Typing events are relayed to fellow room members, not broadcast to all clients."""
    url, gw = gateway_server
    room_id = "test-typing-room"
    async with websockets.connect(url) as ws1, \
               websockets.connect(url) as ws2:
        await _register(ws1, "did:key:typer")
        await _register(ws2, "did:key:listener")
        await asyncio.sleep(0.15)  # allow handle_client to register both connections

        # Inject a room containing all currently-connected server-side WebSocket objects
        gw._local_rooms[room_id] = {
            "name": "typing-test",
            "members": set(gw.clients),
            "pinned_messages": [],
            "disappear_ms": 0,
        }

        await ws1.send(json.dumps({"cmd": "typing", "room_id": room_id}))

        # ws2 is a room member and should receive the typing event
        for _ in range(10):
            raw = await asyncio.wait_for(ws2.recv(), timeout=3)
            event = json.loads(raw)
            if event.get("type") == "typing":
                break
        assert event["type"] == "typing"
        assert event["room_id"] == room_id

@pytest.mark.asyncio
async def test_gateway_reaction_broadcast(gateway_server):
    """Verify that reaction commands (mocked) are broadcast."""
    url, gw = gateway_server
    # Mock a room membership so add_reaction doesn't fail on target lookup
    mock_membership = type('obj', (object,), {'cert': 'mock-cert'})
    mock_client = 'mock-client'
    gw.room_memberships["room1"] = (mock_membership, mock_client)

    from unittest.mock import patch
    # Patch add_reaction so we don't need real Pod interaction
    with patch("proxion_messenger_core.reactions.add_reaction") as mock_add:
        mock_add.return_value = type('obj', (object,), {'reaction_message_id': 'react123'})

        async with websockets.connect(url) as ws:
            await _register(ws)
            await ws.send(json.dumps({
                "cmd": "add_reaction",
                "room_id": "room1",
                "message_id": "msg1",
                "emoji": "🔥"
            }))
            
            raw = await ws.recv()
            event = json.loads(raw)
            # Skip config event sent on connect
            if event.get("type") == "config":
                raw = await ws.recv()
                event = json.loads(raw)
            assert event["type"] == "reaction_added"
            assert event["emoji"] == "🔥"
            assert event["reaction_message_id"] == "react123"

@pytest.mark.asyncio
async def test_gateway_get_rooms(gateway_server):
    """Verify that get_rooms returns registered memberships."""
    url, gw = gateway_server
    # Inject a membership
    gw.room_memberships["room123"] = (type('obj', (object,), {'room_id': 'room-name'}), 'mock-client')

    async with websockets.connect(url) as ws:
        await _register(ws)
        # Send command first, then skip any leading config event in the response
        await ws.send(json.dumps({"cmd": "get_rooms"}))
        resp = await recv_skip_config(ws)
        assert resp["type"] == "rooms"
        assert len(resp["rooms"]) == 1
        assert resp["rooms"][0]["id"] == "room123"
