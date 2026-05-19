"""Tests for WebID profile management."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from proxion_messenger_core.profile import WebIdProfile, get_profile, update_profile


@pytest.mark.asyncio
async def test_get_profile_parses_name_and_avatar():
    """get_profile should parse foaf:name and foaf:img from Turtle response."""
    webid = "https://alice.example/profile#me"
    turtle_response = b"""
    @prefix foaf: <http://xmlns.com/foaf/0.1/> .

    <https://alice.example/profile#me> a foaf:Person ;
        foaf:name "Alice" ;
        foaf:img <https://alice.example/avatar.jpg> ;
        foaf:bio "Developer and open web advocate" .
    """

    with patch("proxion_messenger_core.network.async_safe_get", AsyncMock(return_value=turtle_response)):
        profile = await get_profile(webid)

    assert profile.webid == webid
    assert profile.name == "Alice"
    assert profile.avatar_url == "https://alice.example/avatar.jpg"
    assert profile.bio == "Developer and open web advocate"


@pytest.mark.asyncio
async def test_get_profile_returns_minimal_on_404():
    """get_profile should return minimal profile if fetch fails (non-2xx → NetworkError)."""
    from proxion_messenger_core.network import NetworkError
    webid = "https://alice.example/profile#me"

    with patch("proxion_messenger_core.network.async_safe_get", AsyncMock(side_effect=NetworkError("404"))):
        profile = await get_profile(webid)

    assert profile.webid == webid
    assert profile.name is None
    assert profile.avatar_url is None


@pytest.mark.asyncio
async def test_get_profile_no_metadata_returns_minimal():
    """get_profile should return minimal profile if no metadata found in Turtle."""
    webid = "https://bob.example/profile#me"
    turtle_response = b"""
    @prefix foaf: <http://xmlns.com/foaf/0.1/> .

    <https://bob.example/profile#me> a foaf:Person .
    """

    with patch("proxion_messenger_core.network.async_safe_get", AsyncMock(return_value=turtle_response)):
        profile = await get_profile(webid)

    assert profile.webid == webid
    assert profile.name is None
    assert profile.avatar_url is None


@pytest.mark.asyncio
async def test_get_profile_handles_exception():
    """get_profile should return minimal profile on exception."""
    webid = "https://error.example/profile#me"

    with patch("proxion_messenger_core.network.async_safe_get", AsyncMock(side_effect=Exception("Network error"))):
        profile = await get_profile(webid)

    assert profile.webid == webid
    assert profile.name is None


@pytest.mark.asyncio
async def test_update_profile_puts_turtle():
    """update_profile should PUT a Turtle document with provided fields."""
    webid = "https://alice.example/profile#me"
    mock_client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()
    mock_client.put.return_value = mock_response
    
    await update_profile(
        mock_client,
        webid,
        name="Alice",
        avatar_url="https://alice.example/avatar.jpg",
        bio="Developer",
    )
    
    # Verify PUT was called with correct parameters
    mock_client.put.assert_called_once()
    call_args = mock_client.put.call_args
    
    assert call_args[0][0] == webid
    assert "foaf:name" in call_args[1]["content"]
    assert "Alice" in call_args[1]["content"]
    assert "foaf:img" in call_args[1]["content"]
    assert "avatar.jpg" in call_args[1]["content"]
    assert call_args[1]["headers"]["Content-Type"] == "text/turtle"


@pytest.mark.asyncio
async def test_update_profile_partial_fields():
    """update_profile should handle partial field updates."""
    webid = "https://bob.example/profile#me"
    mock_client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()
    mock_client.put.return_value = mock_response
    
    await update_profile(
        mock_client,
        webid,
        name="Bob",
    )
    
    call_args = mock_client.put.call_args
    assert "foaf:name" in call_args[1]["content"]
    assert "Bob" in call_args[1]["content"]
    # Other fields should not be in the document
    assert "foaf:img" not in call_args[1]["content"] or "foaf:img <" not in call_args[1]["content"]
