import json
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

from proxion_messenger_core.notifications import subscribe_to_resource, watch_stash_uri
from proxion_messenger_core.solid_client import SolidClient

@pytest.fixture
def mock_client():
    client = MagicMock(spec=SolidClient)
    client._resolver = MagicMock()
    client._resolver.resolve.return_value = "http://pod/resource"
    return client

@pytest.mark.asyncio
async def test_subscribe_request_is_well_formed(mock_client):
    # Mock discovery
    mock_client.get.return_value = json.dumps({"receiveFrom": "ws://css/notifications"})
    
    mock_websocket = AsyncMock()
    
    with patch("websockets.connect", return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_websocket))):
        # We need subscribe_to_resource to connect, send, then we can cancel it
        task = asyncio.create_task(subscribe_to_resource(mock_client, "stash://res", AsyncMock(), "http://css"))
        
        await asyncio.sleep(0.1) # Let it connect and send
        
        sent_msg = json.loads(mock_websocket.send.call_args[0][0])
        assert sent_msg["type"] == "Subscribe"
        assert sent_msg["topic"] == "http://pod/resource"
        
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

@pytest.mark.asyncio
async def test_notification_callback_invoked(mock_client):
    mock_client.get.return_value = json.dumps({"receiveFrom": "ws://css/notifications"})
    
    mock_websocket = AsyncMock()
    # Mock __aiter__ to yield one message then stop
    mock_websocket.__aiter__.return_value = ["{\"type\":\"Update\",\"topic\":\"...\"}"]
    
    callback = AsyncMock()
    
    with patch("websockets.connect", return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_websocket))):
        await subscribe_to_resource(mock_client, "stash://res", callback, "http://css")
        
        callback.assert_called_once_with("{\"type\":\"Update\",\"topic\":\"...\"}")

@pytest.mark.asyncio
async def test_fallback_on_discovery_failure(mock_client):
    # Discovery returns 404 or empty
    mock_client.get.return_value = "{}"
    
    callback = AsyncMock()
    with patch("websockets.connect") as mock_connect:
        await subscribe_to_resource(mock_client, "stash://res", callback, "http://css")
        assert mock_connect.call_count == 0

@pytest.mark.asyncio
async def test_watch_stash_uri_triggers_on_change(mock_client):
    mock_client.get.return_value = json.dumps({"receiveFrom": "ws://css/notifications"})
    mock_websocket = AsyncMock()
    mock_websocket.__aiter__.return_value = ["any message"]
    
    on_change = AsyncMock()
    
    with patch("websockets.connect", return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_websocket))):
        await watch_stash_uri(mock_client, "stash://res", on_change, "http://css")
        on_change.assert_called_once()
