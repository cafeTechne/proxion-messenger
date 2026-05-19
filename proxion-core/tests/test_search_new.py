"""Tests for thread search functions."""

import json
from unittest.mock import AsyncMock

import pytest

from proxion_messenger_core.search import SearchResult, search_thread, search_all_threads


@pytest.fixture
def mock_stash():
    """Create a mock stash with async methods."""
    stash = AsyncMock()
    stash.put = AsyncMock()
    stash.get = AsyncMock()
    stash.delete = AsyncMock()
    stash.list = AsyncMock(return_value=[])
    return stash


@pytest.mark.asyncio
async def test_search_thread_finds_matches(mock_stash):
    """search_thread should find messages matching the query."""
    thread_id = "thread-123"
    messages = {
        "messages": [
            {
                "id": "msg-1",
                "body": "Hello world",
                "from_webid": "https://alice.example/",
                "timestamp": "2026-04-12T10:00:00Z",
            },
            {
                "id": "msg-2",
                "body": "Goodbye world",
                "from_webid": "https://bob.example/",
                "timestamp": "2026-04-12T11:00:00Z",
            },
        ]
    }
    
    mock_stash.get.return_value = json.dumps(messages).encode()
    
    results = await search_thread(mock_stash, thread_id, "world")
    
    assert len(results) >= 1
    assert any("world" in r.snippet for r in results)


@pytest.mark.asyncio
async def test_search_thread_case_insensitive(mock_stash):
    """search_thread should be case-insensitive by default."""
    thread_id = "thread-456"
    messages = {
        "messages": [
            {
                "id": "msg-1",
                "body": "Hello WORLD",
                "from_webid": "https://alice.example/",
                "timestamp": "2026-04-12T10:00:00Z",
            },
        ]
    }
    
    mock_stash.get.return_value = json.dumps(messages).encode()
    
    results = await search_thread(mock_stash, thread_id, "world", case_sensitive=False)
    
    assert len(results) == 1
    assert "WORLD" in results[0].snippet


@pytest.mark.asyncio
async def test_search_thread_returns_empty_when_no_matches(mock_stash):
    """search_thread should return empty list when no matches found."""
    thread_id = "thread-789"
    messages = {
        "messages": [
            {
                "id": "msg-1",
                "body": "Hello",
                "from_webid": "https://alice.example/",
                "timestamp": "2026-04-12T10:00:00Z",
            },
        ]
    }
    
    mock_stash.get.return_value = json.dumps(messages).encode()
    
    results = await search_thread(mock_stash, thread_id, "nonexistent")
    
    assert len(results) == 0


@pytest.mark.asyncio
async def test_search_thread_respects_limit(mock_stash):
    """search_thread should stop searching after reaching limit."""
    thread_id = "thread-101"
    messages = {
        "messages": [
            {
                "id": f"msg-{i}",
                "body": "test message " + str(i),
                "from_webid": "https://alice.example/",
                "timestamp": f"2026-04-12T{10+i:02d}:00:00Z",
            }
            for i in range(100)
        ]
    }
    
    mock_stash.get.return_value = json.dumps(messages).encode()
    
    results = await search_thread(mock_stash, thread_id, "test", limit=10)
    
    assert len(results) <= 10


@pytest.mark.asyncio
async def test_search_all_threads_merges_results(mock_stash):
    """search_all_threads should merge results from multiple threads."""
    messages_1 = {
        "messages": [
            {
                "id": "msg-1",
                "body": "hello from thread 1",
                "from_webid": "https://alice.example/",
                "timestamp": "2026-04-12T10:00:00Z",
            },
        ]
    }
    messages_2 = {
        "messages": [
            {
                "id": "msg-2",
                "body": "hello from thread 2",
                "from_webid": "https://bob.example/",
                "timestamp": "2026-04-12T11:00:00Z",
            },
        ]
    }
    
    async def get_impl(key):
        if "thread-1" in key:
            return json.dumps(messages_1).encode()
        elif "thread-2" in key:
            return json.dumps(messages_2).encode()
        return None
    
    mock_stash.get.side_effect = get_impl
    
    results = await search_all_threads(mock_stash, "hello", ["thread-1", "thread-2"])
    
    assert len(results) >= 1
    assert any("hello" in r.snippet for r in results)
