"""Tests for link previews."""
from __future__ import annotations

import pytest
import respx
from httpx import Response
from unittest.mock import patch
from proxion_messenger_core.linkpreview import is_safe_url, fetch_link_preview, extract_urls

_FAKE_ADDRINFO = [(None, None, None, None, ("93.184.216.34", 0))]

def test_extract_urls():
    text = "Check this: https://example.com/foo and http://test.org."
    urls = extract_urls(text)
    assert urls == ["https://example.com/foo", "http://test.org"]

def test_is_safe_url():
    # Public IP literal — no DNS needed; resolves to a non-private address
    assert is_safe_url("https://1.1.1.1/") is True
    assert is_safe_url("http://127.0.0.1/admin") is False
    assert is_safe_url("https://localhost:8080") is False
    assert is_safe_url("http://192.168.1.1") is False
    # Credential bypass: urlparse strips userinfo before resolution
    assert is_safe_url("http://user@127.0.0.1/") is False
    # Link-local / cloud metadata endpoint
    assert is_safe_url("http://169.254.169.254/") is False

@pytest.mark.asyncio
@respx.mock
async def test_fetch_link_preview_success():
    url = "https://example.com"
    html = """
    <html>
        <head>
            <meta property="og:title" content="Example Domain">
            <meta property="og:description" content="This is a test description">
            <meta property="og:image" content="https://example.com/image.png">
        </head>
    </html>
    """
    respx.get(url).mock(return_value=Response(200, text=html))
    
    preview = await fetch_link_preview(url)
    assert preview["title"] == "Example Domain"
    assert preview["description"] == "This is a test description"
    assert preview["image"] == "https://example.com/image.png"

@pytest.mark.asyncio
@respx.mock
async def test_fetch_link_preview_fallback_title():
    url = "https://no-og.com"
    html = "<html><head><title>Just a Title</title></head></html>"
    respx.get(url).mock(return_value=Response(200, text=html))
    with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_FAKE_ADDRINFO):
        preview = await fetch_link_preview(url)
    assert preview["title"] == "Just a Title"
    assert preview["description"] == ""

@pytest.mark.asyncio
async def test_fetch_link_preview_unsafe():
    url = "http://127.0.0.1/hack"
    preview = await fetch_link_preview(url)
    assert preview is None
