"""Unit tests for presence.py — user presence tracking."""

import pytest
import json
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from proxion_messenger_core.presence import (
    PresenceDoc, set_presence, get_presence,
    PRESENCE_PATH,
)
from proxion_messenger_core.solid_client import SolidError


@pytest.fixture
def mock_pod_client():
    """Mock SolidClient."""
    return MagicMock()


def test_presence_doc_structure():
    """Test PresenceDoc dataclass."""
    doc = PresenceDoc(
        status="online",
        display_name="Alice",
        updated_at="2026-04-11T10:00:00+00:00",
    )
    
    assert doc.status == "online"
    assert doc.display_name == "Alice"
    assert doc.updated_at == "2026-04-11T10:00:00+00:00"


def test_set_presence_puts_json(mock_pod_client):
    """Test set_presence() PUTs presence JSON."""
    with patch("proxion_messenger_core.presence.datetime") as mock_datetime:
        mock_now = datetime(2026, 4, 11, 10, 0, 0, tzinfo=timezone.utc)
        mock_datetime.now.return_value = mock_now
        
        set_presence(mock_pod_client, "online", "Alice")
        
        mock_pod_client.put.assert_called_once()
        call_args = mock_pod_client.put.call_args[0]
        assert call_args[0] == PRESENCE_PATH
        
        # Parse the JSON
        data = json.loads(call_args[1].decode("utf-8"))
        assert data["status"] == "online"
        assert data["display_name"] == "Alice"
        assert "updated_at" in data


def test_set_presence_content_type():
    """Test set_presence() content is JSON-encoded."""
    mock_client = MagicMock()
    
    set_presence(mock_client, "away", "Bob")
    
    call_args = mock_client.put.call_args[0]
    content = call_args[1]
    
    # Should be bytes
    assert isinstance(content, bytes)
    
    # Should be valid JSON
    data = json.loads(content.decode("utf-8"))
    assert data["status"] == "away"


def test_get_presence_parses_json(mock_pod_client):
    """Test get_presence() parses JSON from pod."""
    response_data = {
        "status": "busy",
        "display_name": "Charlie",
        "updated_at": "2026-04-11T09:30:00+00:00",
    }
    mock_pod_client.get.return_value = json.dumps(response_data).encode("utf-8")
    
    doc = get_presence(mock_pod_client)
    
    assert doc.status == "busy"
    assert doc.display_name == "Charlie"
    assert doc.updated_at == "2026-04-11T09:30:00+00:00"


def test_get_presence_default_on_404(mock_pod_client):
    """Test get_presence() returns 'offline' doc on 404."""
    mock_pod_client.get.side_effect = SolidError("404 Not Found")
    
    doc = get_presence(mock_pod_client)
    
    assert doc.status == "offline"
    assert doc.display_name == "Unknown"


def test_get_presence_default_on_parse_error(mock_pod_client):
    """Test get_presence() returns 'offline' doc on JSON parse error."""
    mock_pod_client.get.return_value = b"not valid json"
    
    doc = get_presence(mock_pod_client)
    
    assert doc.status == "offline"
    assert doc.display_name == "Unknown"


def test_get_presence_default_on_missing_fields(mock_pod_client):
    """Test get_presence() uses defaults for missing fields."""
    response_data = {
        "status": "online",
        # missing display_name and updated_at
    }
    mock_pod_client.get.return_value = json.dumps(response_data).encode("utf-8")
    
    doc = get_presence(mock_pod_client)
    
    assert doc.status == "online"
    assert doc.display_name == "Unknown"  # Default


def test_get_presence_custom_stash_uri(mock_pod_client):
    """Test get_presence() accepts custom stash_uri."""
    mock_pod_client.get.return_value = json.dumps({
        "status": "away",
        "display_name": "David",
        "updated_at": "2026-04-11T00:00:00+00:00",
    }).encode("utf-8")
    
    doc = get_presence(mock_pod_client, stash_uri="stash://custom/presence.json")
    
    mock_pod_client.get.assert_called_once_with("stash://custom/presence.json")
    assert doc.status == "away"


def test_set_presence_all_statuses():
    """Test set_presence() accepts all valid statuses."""
    for status in ["online", "away", "busy", "offline"]:
        mock_client = MagicMock()
        set_presence(mock_client, status, "User")
        
        call_args = mock_client.put.call_args[0]
        data = json.loads(call_args[1].decode("utf-8"))
        assert data["status"] == status


# ---------------------------------------------------------------------------
# Round 24 — status_text and avatar_url additions
# ---------------------------------------------------------------------------

def test_set_presence_with_status_text():
    """set_presence stores status_text in the JSON document."""
    mock_client = MagicMock()
    set_presence(mock_client, "online", "Alice", status_text="Playing Elden Ring")

    call_args = mock_client.put.call_args[0]
    data = json.loads(call_args[1].decode("utf-8"))
    assert data["status_text"] == "Playing Elden Ring"


def test_set_presence_with_avatar_url():
    """set_presence stores avatar_url in the JSON document."""
    mock_client = MagicMock()
    set_presence(mock_client, "online", "Bob", avatar_url="stash://profile/avatar.png")

    call_args = mock_client.put.call_args[0]
    data = json.loads(call_args[1].decode("utf-8"))
    assert data["avatar_url"] == "stash://profile/avatar.png"


def test_get_presence_parses_status_text_and_avatar():
    """get_presence deserializes status_text and avatar_url from stored JSON."""
    mock_client = MagicMock()
    mock_client.get.return_value = json.dumps({
        "status": "busy",
        "display_name": "Carol",
        "updated_at": "2026-04-11T00:00:00+00:00",
        "status_text": "In a meeting",
        "avatar_url": "stash://profile/av.png",
    }).encode("utf-8")

    doc = get_presence(mock_client)
    assert doc.status_text == "In a meeting"
    assert doc.avatar_url == "stash://profile/av.png"
