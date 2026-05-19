"""Unit tests for identity.py."""

import pytest
from unittest.mock import MagicMock, patch
import json

from proxion_messenger_core.identity import publish_identity, fetch_identity, IdentityCard
from proxion_messenger_core.solid_client import SolidError


@pytest.fixture
def mock_solid_client():
    """Mock SolidClient."""
    client = MagicMock()
    client.put = MagicMock()
    client.get = MagicMock()
    return client


def test_publish_identity(mock_solid_client):
    """Test publish_identity writes to Pod."""
    card = IdentityCard(
        display_name="Alice",
        avatar_url="https://example.com/alice.jpg",
        bio="Test user"
    )
    
    publish_identity(mock_solid_client, card)
    
    # Verify put was called
    assert mock_solid_client.put.called
    call_args = mock_solid_client.put.call_args
    
    # Check path and content
    path = call_args[0][0]
    content = call_args[0][1]
    
    assert "identity" in path
    assert isinstance(content, bytes)
    
    data = json.loads(content.decode("utf-8"))
    assert data["display_name"] == "Alice"
    assert data["bio"] == "Test user"


def test_fetch_identity(mock_solid_client):
    """Test fetch_identity reads from Pod."""
    identity_data = {
        "display_name": "Alice",
        "avatar_url": "https://example.com/alice.jpg",
        "bio": "Test user",
        "proxion_version": "0.1.0"
    }
    mock_solid_client.get.return_value = json.dumps(identity_data).encode("utf-8")
    
    result = fetch_identity(mock_solid_client)
    
    assert result.display_name == "Alice"
    assert result.bio == "Test user"


def test_fetch_identity_404_returns_default(mock_solid_client):
    """Test fetch_identity returns default when card not found."""
    mock_solid_client.get.side_effect = SolidError("404 Not Found", 404)
    
    result = fetch_identity(mock_solid_client)
    
    assert result.display_name == "Unknown"


def test_fetch_identity_invalid_json(mock_solid_client):
    """Test fetch_identity handles invalid JSON gracefully."""
    mock_solid_client.get.return_value = b"not valid json"
    
    result = fetch_identity(mock_solid_client)
    
    assert result.display_name == "Unknown"


def test_publish_identity_minimal(mock_solid_client):
    """Test publish_identity with minimal fields."""
    card = IdentityCard(display_name="Bob")
    
    publish_identity(mock_solid_client, card)
    
    assert mock_solid_client.put.called
    content = mock_solid_client.put.call_args[0][1]
    data = json.loads(content.decode("utf-8"))
    
    assert data["display_name"] == "Bob"
    assert data["avatar_url"] is None


def test_identity_card_has_did_field():
    """Test IdentityCard has did field and defaults to None."""
    card = IdentityCard(display_name="Alice")
    
    assert hasattr(card, "did")
    assert card.did is None
    
    card_with_did = IdentityCard(display_name="Bob", did="did:key:z6Mk...")
    assert card_with_did.did == "did:key:z6Mk..."
