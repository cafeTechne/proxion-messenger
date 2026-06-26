"""Tests for room discovery functions."""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock

from proxion_messenger_core.room import list_public_rooms, search_rooms, RoomConfig
from proxion_messenger_core.solid_client import SolidError


@pytest.fixture
def mock_pod_client():
    """Mock SolidClient."""
    return MagicMock()


def test_list_public_rooms_returns_all(mock_pod_client):
    """list_public_rooms returns all directory entries."""
    room1_data = {
        "room_id": "room1",
        "name": "General",
        "owner_webid": "alice@pod.com",
        "pod_url": "http://alice.pod",
        "stash_root": "stash://rooms/room1/",
        "created_at": "2024-01-01T00:00:00Z",
        "public": True,
        "topic": "General chat",
        "description": "General discussion",
    }
    room2_data = {
        "room_id": "room2",
        "name": "Music",
        "owner_webid": "bob@pod.com",
        "pod_url": "http://bob.pod",
        "stash_root": "stash://rooms/room2/",
        "created_at": "2024-01-02T00:00:00Z",
        "public": True,
        "topic": "Music discussion",
        "description": "Share your favorite songs",
    }
    
    # Mock the list and get methods
    mock_pod_client.list.return_value = [
        "stash://rooms/directory/room1.json",
        "stash://rooms/directory/room2.json",
    ]
    mock_pod_client.get.side_effect = [
        json.dumps(room1_data).encode("utf-8"),
        json.dumps(room2_data).encode("utf-8"),
    ]
    
    rooms = list_public_rooms(mock_pod_client)
    
    assert len(rooms) == 2
    assert rooms[0].name == "General"
    assert rooms[1].name == "Music"


def test_list_public_rooms_empty_directory_returns_empty(mock_pod_client):
    """list_public_rooms returns empty list for empty directory."""
    mock_pod_client.list.return_value = []
    
    rooms = list_public_rooms(mock_pod_client)
    
    assert rooms == []


def test_search_rooms_filters_by_name(mock_pod_client):
    """search_rooms filters by room name."""
    room1_data = {
        "room_id": "room1",
        "name": "Gaming Lounge",
        "owner_webid": "alice@pod.com",
        "pod_url": "http://alice.pod",
        "stash_root": "stash://rooms/room1/",
        "created_at": "2024-01-01T00:00:00Z",
        "public": True,
        "topic": None,
        "description": None,
    }
    room2_data = {
        "room_id": "room2",
        "name": "Music Fans",
        "owner_webid": "bob@pod.com",
        "pod_url": "http://bob.pod",
        "stash_root": "stash://rooms/room2/",
        "created_at": "2024-01-02T00:00:00Z",
        "public": True,
        "topic": None,
        "description": None,
    }
    room3_data = {
        "room_id": "room3",
        "name": "General",
        "owner_webid": "charlie@pod.com",
        "pod_url": "http://charlie.pod",
        "stash_root": "stash://rooms/room3/",
        "created_at": "2024-01-03T00:00:00Z",
        "public": True,
        "topic": None,
        "description": None,
    }
    
    mock_pod_client.list.return_value = [
        "stash://rooms/directory/room1.json",
        "stash://rooms/directory/room2.json",
        "stash://rooms/directory/room3.json",
    ]
    mock_pod_client.get.side_effect = [
        json.dumps(room1_data).encode("utf-8"),
        json.dumps(room2_data).encode("utf-8"),
        json.dumps(room3_data).encode("utf-8"),
    ]
    
    results = search_rooms(mock_pod_client, "music")
    
    assert len(results) == 1
    assert results[0].name == "Music Fans"


def test_search_rooms_matches_topic(mock_pod_client):
    """search_rooms matches room topic."""
    room1_data = {
        "room_id": "room1",
        "name": "Gaming Lounge",
        "owner_webid": "alice@pod.com",
        "pod_url": "http://alice.pod",
        "stash_root": "stash://rooms/room1/",
        "created_at": "2024-01-01T00:00:00Z",
        "public": True,
        "topic": "Gaming chat",
        "description": None,
    }
    
    mock_pod_client.list.return_value = [
        "stash://rooms/directory/room1.json",
    ]
    mock_pod_client.get.return_value = json.dumps(room1_data).encode("utf-8")
    
    results = search_rooms(mock_pod_client, "gaming")
    
    assert len(results) == 1
    assert results[0].name == "Gaming Lounge"
