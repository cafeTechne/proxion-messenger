import pytest
from unittest.mock import MagicMock
from proxion_messenger_core.identity import IdentityCard, upload_avatar, get_avatar, publish_identity, fetch_identity
from proxion_messenger_core.solid_client import SolidClient

@pytest.fixture
def mock_client():
    client = MagicMock(spec=SolidClient)
    client._resolver = MagicMock()
    return client

def test_upload_avatar_returns_stash_uri(mock_client):
    uri = upload_avatar(mock_client, b"image data", "image/png")
    assert uri == "stash://profile/avatar.png"
    mock_client.put.assert_called_once_with("stash://profile/avatar.png", b"image data", content_type="image/png")

def test_get_avatar_returns_bytes(mock_client):
    mock_client.get.return_value = b"image data"
    data = get_avatar(mock_client, "stash://profile/avatar.png")
    assert data == b"image data"

def test_publish_identity_includes_avatar(mock_client):
    card = IdentityCard(display_name="Alice", avatar_url="stash://profile/avatar.png")
    publish_identity(mock_client, card)
    
    args, kwargs = mock_client.put.call_args
    import json
    data = json.loads(args[1].decode("utf-8"))
    assert data["avatar_url"] == "stash://profile/avatar.png"

def test_fetch_identity_parses_avatar(mock_client):
    import json
    mock_client.get.return_value = json.dumps({
        "display_name": "Bob",
        "avatar_url": "stash://profile/avatar.jpg"
    }).encode("utf-8")
    
    card = fetch_identity(mock_client)
    assert card.display_name == "Bob"
    assert card.avatar_url == "stash://profile/avatar.jpg"
