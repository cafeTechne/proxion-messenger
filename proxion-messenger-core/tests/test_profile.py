"""Tests for WebID profile management."""

import re

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


def test_escape_turtle_literal_neutralises_breakout():
    """The literal-escape helper defeats an attempt to terminate the literal."""
    from proxion_messenger_core.profile import _escape_turtle_literal

    attack = '" . <https://evil.example/#e> <http://x/p> "pwned'
    escaped = _escape_turtle_literal(attack)
    # No unescaped quote survives, so the literal cannot be closed early.
    assert re.search(r'(^|[^\\])"', escaped) is None
    # Backslash, newline, carriage-return and tab become two-character escapes.
    assert _escape_turtle_literal("a\\b") == "a\\\\b"
    assert _escape_turtle_literal("a\nb") == "a\\nb"
    assert _escape_turtle_literal("a\r\nb") == "a\\r\\nb"
    assert _escape_turtle_literal("a\tb") == "a\\tb"
    # Remaining control characters are stripped rather than emitted raw.
    assert _escape_turtle_literal("a\x00b\x07c") == "abc"


@pytest.mark.asyncio
async def test_update_profile_escapes_literal_injection():
    """A quote/newline in name or bio must not close the literal and inject triples."""
    webid = "https://alice.example/profile#me"
    mock_client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()
    mock_client.put.return_value = mock_response

    attack = '" . <https://evil.example/#e> a foaf:Person ; foaf:name "pwned'
    await update_profile(
        mock_client,
        webid,
        name=attack,
        bio="line1\nline2",
    )

    content = mock_client.put.call_args[1]["content"]
    # The attack's leading quote is escaped, so the foaf:name literal is not
    # terminated early and the injected statements never become real triples.
    assert 'foaf:name "\\"' in content
    # Newline in the bio is escaped, not emitted raw inside the literal.
    assert "line1\\nline2" in content
    assert '"line1\nline2"' not in content

    # Parse the emitted document: the injected subject must not exist as a real
    # node, and the whole attack payload must survive only as the name literal.
    import rdflib
    g = rdflib.Graph()
    g.parse(data=content, format="turtle")
    evil = rdflib.URIRef("https://evil.example/#e")
    assert (evil, None, None) not in g
    assert (None, None, evil) not in g
    name_obj = g.value(rdflib.URIRef(webid), rdflib.URIRef("http://xmlns.com/foaf/0.1/name"))
    assert str(name_obj) == attack


@pytest.mark.asyncio
async def test_update_profile_rejects_unsafe_webid():
    """A webid with characters unsafe for a Turtle IRI is rejected, nothing written."""
    mock_client = AsyncMock()
    with pytest.raises(ValueError):
        await update_profile(
            mock_client,
            'https://alice.example/#me> a foaf:Person . <https://evil.example/#e',
            name="Alice",
        )
    mock_client.put.assert_not_called()


@pytest.mark.asyncio
async def test_update_profile_rejects_unsafe_avatar_url():
    """An avatar_url with characters unsafe for a Turtle IRI is rejected."""
    webid = "https://alice.example/profile#me"
    mock_client = AsyncMock()
    with pytest.raises(ValueError):
        await update_profile(
            mock_client,
            webid,
            avatar_url="https://alice.example/a.jpg> . <https://evil.example/#e",
        )
    mock_client.put.assert_not_called()
