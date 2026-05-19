"""Tests for gateway presence tracking (Round 32, B4)."""

import asyncio
import json
import pytest
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from pathlib import Path
import tempfile


@pytest.fixture
def temp_dir():
    """Create temporary directory for test artifacts."""
    with tempfile.TemporaryDirectory() as td:
        yield td


@pytest.fixture
def agent_state(temp_dir):
    """Create a test agent state."""
    return AgentState.generate()



@pytest.fixture
def gateway(agent_state):
    """Create a test gateway with in-memory config."""
    config = GatewayConfig(host="127.0.0.1", port=0)
    return ProxionGateway(
        agent=agent_state,
        dm_clients={},
        room_memberships={},
        config=config
    )


class MockWebSocket:
    """Mock WebSocket for testing."""
    def __init__(self):
        self.messages = []
        self.connected = True

    async def send(self, msg):
        """Simulate sending a message."""
        if self.connected:
            self.messages.append(msg)

    async def recv(self):
        """Simulate receiving a message."""
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(0.1)
        return None

    def close(self):
        """Simulate closing connection."""
        self.connected = False


@pytest.mark.asyncio
async def test_register_sets_online_presence(gateway):
    """Test that registering a client sets their presence to online."""
    ws = MockWebSocket()
    gateway.clients.add(ws)
    
    # Register client
    await gateway.process_command(ws, {
        "cmd": "register",
        "webid": "test-webid-123"
    })
    
    # Check presence is set to online
    assert "test-webid-123" in gateway._user_presence
    assert gateway._user_presence["test-webid-123"]["status"] == "online"
    
    # Check broadcast message
    messages = [json.loads(m) for m in ws.messages if ws.messages]
    presence_updates = [m for m in messages if m.get("type") == "presence_update"]
    assert len(presence_updates) > 0
    assert presence_updates[0]["webid"] == "test-webid-123"
    assert presence_updates[0]["status"] == "online"


@pytest.mark.asyncio
async def test_set_presence_command(gateway):
    """Test set_presence command changes status."""
    ws = MockWebSocket()
    gateway.clients.add(ws)
    
    # First register
    await gateway.process_command(ws, {
        "cmd": "register",
        "webid": "test-webid-456"
    })
    
    # Verify initial state is online
    assert gateway._client_webids[ws] == "test-webid-456"
    assert gateway._user_presence["test-webid-456"]["status"] == "online"
    
    # Now change presence to away
    await gateway.process_command(ws, {
        "cmd": "set_presence",
        "status": "away"
    })
    
    # Check presence updated
    assert gateway._user_presence["test-webid-456"]["status"] == "away"


@pytest.mark.asyncio
async def test_set_presence_invalid_status_ignored(gateway):
    """Test that invalid presence status is ignored."""
    ws = MockWebSocket()
    gateway.clients.add(ws)
    
    await gateway.process_command(ws, {
        "cmd": "register",
        "webid": "test-webid-789"
    })
    
    # Try invalid status
    ws.messages.clear()
    await gateway.process_command(ws, {
        "cmd": "set_presence",
        "status": "invalid_status"
    })
    
    # Presence should not change
    assert gateway._user_presence["test-webid-789"]["status"] == "online"


@pytest.mark.asyncio
async def test_get_presence_command(gateway):
    """Test get_presence command retrieves presence data."""
    ws = MockWebSocket()
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "did:key:presencetest1"

    # Set presence for a user
    gateway._user_presence["remote-user-001"] = {
        "status": "busy",
        "updated_at": "2026-04-15T12:00:00+00:00"
    }

    # Query presence
    await gateway.process_command(ws, {
        "cmd": "get_presence",
        "webid": "remote-user-001"
    })
    
    # Check response
    messages = [json.loads(m) for m in ws.messages if ws.messages]
    responses = [m for m in messages if m.get("type") == "presence"]
    assert len(responses) > 0
    assert responses[0]["webid"] == "remote-user-001"
    assert responses[0]["status"] == "busy"


@pytest.mark.asyncio
async def test_get_presence_offline_default(gateway):
    """Test that get_presence returns offline for unknown user."""
    ws = MockWebSocket()
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "did:key:presencetest2"

    # Query presence for non-existent user
    await gateway.process_command(ws, {
        "cmd": "get_presence",
        "webid": "unknown-user"
    })
    
    # Check response defaults to offline
    messages = [json.loads(m) for m in ws.messages if ws.messages]
    responses = [m for m in messages if m.get("type") == "presence"]
    assert len(responses) > 0
    assert responses[0]["status"] == "offline"


@pytest.mark.asyncio
async def test_client_disconnect_sets_offline(gateway):
    """Test that disconnecting a client sets presence to offline."""
    ws = MockWebSocket()
    gateway.clients.add(ws)
    
    # Register
    await gateway.process_command(ws, {
        "cmd": "register",
        "webid": "disconnect-test-123"
    })
    
    assert gateway._user_presence["disconnect-test-123"]["status"] == "online"
    
    # Simulate disconnect
    gateway.clients.discard(ws)
    webid = gateway._client_webids.pop(ws, None)
    if webid:
        gateway._webid_sockets.pop(webid, None)
        gateway._user_presence[webid] = {
            "status": "offline",
            "updated_at": "2026-04-15T12:01:00+00:00"
        }
    
    # Check presence is offline
    assert gateway._user_presence["disconnect-test-123"]["status"] == "offline"


@pytest.mark.asyncio
async def test_all_presence_command(gateway):
    """Test get_all_presence returns presence data for caller's contacts."""
    from unittest.mock import MagicMock
    ws = MockWebSocket()
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "did:key:presencetest3"

    # Add multiple users
    gateway._user_presence["user1"] = {"status": "online"}
    gateway._user_presence["user2"] = {"status": "away"}
    gateway._user_presence["user3"] = {"status": "offline"}

    # Mock the store so user1/user2/user3 appear as contacts of the caller
    mock_store = MagicMock()
    mock_store.list_relationships.return_value = [
        {"peer_did": "user1"},
        {"peer_did": "user2"},
        {"peer_did": "user3"},
    ]
    gateway._store = mock_store

    # Query all presence
    await gateway.process_command(ws, {
        "cmd": "get_all_presence"
    })
    
    # Check response
    messages = [json.loads(m) for m in ws.messages if ws.messages]
    responses = [m for m in messages if m.get("type") == "all_presence"]
    assert len(responses) > 0
    assert "user1" in responses[0]["presence"]
    assert "user2" in responses[0]["presence"]
    assert "user3" in responses[0]["presence"]
    assert responses[0]["presence"]["user1"]["status"] == "online"
    assert responses[0]["presence"]["user2"]["status"] == "away"
