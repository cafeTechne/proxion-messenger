import json
import pytest
import asyncio
import time
from unittest.mock import MagicMock, AsyncMock
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.local_store import LocalStore

@pytest.fixture
def mock_agent():
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x00" * 32
    agent.identity_key = MagicMock()
    return agent

@pytest.fixture
def gateway(mock_agent, tmp_path):
    db_path = tmp_path / "test.db"
    config = GatewayConfig(db_path=str(db_path))
    gw = ProxionGateway(mock_agent, {}, {}, config)
    return gw

@pytest.mark.asyncio
async def test_auth_gate_enforcement(gateway):
    """Verify that unauthenticated commands are rejected."""
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock()
    
    # Try a protected command
    await gateway.process_command(mock_ws, {"cmd": "send_room", "room_id": "test", "content": "hello"})
    
    # Should receive "Not registered" error
    args, _ = mock_ws.send.call_args
    resp = json.loads(args[0])
    assert resp["type"] == "error"
    assert resp["message"] == "Not registered"

@pytest.mark.asyncio
async def test_get_message_authorization(gateway, tmp_path):
    """Verify that a user cannot fetch messages from threads they are not in."""
    # Setup: Save a message in thread A
    gateway._store.save_message("msg1", "threadA", "room", "userA", "User A", "secret", "2023-01-01T00:00:00Z")
    
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock()
    gateway._client_webids[mock_ws] = "userB" # User B is not in thread A
    
    # Try to fetch message from thread A
    await gateway._handle_get_message(mock_ws, {"message_id": "msg1"})
    
    # Should receive "Unauthorized" error
    args, _ = mock_ws.send.call_args
    resp = json.loads(args[0])
    assert resp["type"] == "error"
    assert resp["message"] == "Unauthorized"

@pytest.mark.asyncio
async def test_room_admin_privilege_escalation(gateway):
    """Verify that only the room owner can perform admin actions."""
    room_id = "room1"
    gateway._local_rooms[room_id] = {
        "name": "Test Room",
        "creator_webid": "owner_wid",
        "members": set()
    }
    
    # Non-owner tries to kick
    mock_ws = AsyncMock()
    gateway._client_webids[mock_ws] = "attacker_wid"
    gateway._local_rooms[room_id]["members"].add(mock_ws)
    
    await gateway._handle_kick_member(mock_ws, {"room_id": room_id, "webid": "somebody"})
    
    args, _ = mock_ws.send.call_args
    resp = json.loads(args[0])
    assert resp["type"] == "error"
    assert "Only the room owner" in resp["message"]

@pytest.mark.asyncio
async def test_webhook_ssrf_protection(gateway):
    """Verify that outgoing webhooks block private IPs."""
    from proxion_messenger_core.network import _resolve_safe_ip

    # Internal IP should be blocked by default
    assert _resolve_safe_ip("http://127.0.0.1/webhook") is None
    assert _resolve_safe_ip("http://169.254.169.254/metadata") is None

    # Public IP should be allowed
    # (Using google.com for test, though it won't actually be called)
    assert _resolve_safe_ip("https://google.com/webhook") is not None

@pytest.mark.asyncio
async def test_storage_quota_enforcement(gateway):
    """Verify that LocalStore enforces byte quotas."""
    store = gateway._store
    thread_id = "heavy_thread"
    
    # Send a massive message (over 50MB)
    large_content = "X" * (51 * 1024 * 1024)
    store.save_message("big_msg", thread_id, "room", "user", "User", large_content, "ts")
    
    # Message should NOT be in the DB
    assert store.get_message("big_msg") is None
