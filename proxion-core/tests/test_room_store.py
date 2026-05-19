"""Unit tests for room_store.py."""

import json
import pytest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from proxion_messenger_core.room import RoomConfig, RoomMembership
from proxion_messenger_core.room_store import RoomStore


@pytest.fixture
def temp_room_dir(tmp_path):
    """Temporary directory for room storage."""
    return tmp_path / "rooms"


@pytest.fixture
def room_store(temp_room_dir):
    """Create a RoomStore instance."""
    return RoomStore(temp_room_dir)


@pytest.fixture
def sample_room():
    """Create a sample room config."""
    room_id = uuid.uuid4().hex
    return RoomConfig(
        room_id=room_id,
        name="Test Room",
        owner_webid="http://localhost:3001/alice/profile/card#me",
        pod_url="http://localhost:3001/alice/",
        stash_root=f"stash://rooms/{room_id}/",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


@pytest.fixture
def mock_cert():
    """Mock RelationshipCertificate."""
    cert = MagicMock()
    cert.certificate_id = uuid.uuid4().hex
    cert.to_dict = MagicMock(return_value={
        "certificate_id": cert.certificate_id,
        "issuer": "http://localhost:3001/alice/profile/card#me",
        "subject": "http://localhost:3002/bob/profile/card#me",
        "capabilities": [],
        "created_at": int(1234567890),
        "expires_at": int(1234567900),
        "wireguard": {},
    })
    return cert


def test_room_store_save_and_load(room_store, sample_room):
    """Test save and load round-trip for RoomConfig."""
    room_store.save_room(sample_room)
    loaded = room_store.load_room(sample_room.room_id)
    
    assert loaded.room_id == sample_room.room_id
    assert loaded.name == sample_room.name
    assert loaded.owner_webid == sample_room.owner_webid


def test_room_store_save_membership(room_store, sample_room, mock_cert):
    """Test save and load round-trip for RoomMembership."""
    membership = RoomMembership(
        room=sample_room,
        cert=mock_cert,
        member_webid="http://localhost:3002/bob/profile/card#me",
    )
    
    room_store.save_membership(membership)
    loaded = room_store.load_membership(sample_room.room_id)
    
    assert loaded.member_webid == membership.member_webid
    assert loaded.room.room_id == sample_room.room_id


def test_room_store_list_rooms(room_store):
    """Test list_rooms returns all saved rooms."""
    rooms = []
    for i in range(3):
        room = RoomConfig(
            room_id=f"room-{i}",
            name=f"Room {i}",
            owner_webid="http://localhost:3001/alice/profile/card#me",
            pod_url="http://localhost:3001/alice/",
            stash_root=f"stash://rooms/room-{i}/",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        room_store.save_room(room)
        rooms.append(room)
    
    loaded = room_store.list_rooms()
    assert len(loaded) == 3
    assert all(r.room_id.startswith("room-") for r in loaded)


def test_room_store_delete_room(room_store, sample_room):
    """Test delete_room removes files from disk."""
    room_store.save_room(sample_room)
    config_path = room_store._config_path(sample_room.room_id)
    assert config_path.exists()
    
    room_store.delete_room(sample_room.room_id)
    assert not config_path.exists()
    assert not room_store._room_dir(sample_room.room_id).exists()


def test_room_store_load_nonexistent(room_store):
    """Test load_room raises FileNotFoundError for nonexistent room."""
    with pytest.raises(FileNotFoundError):
        room_store.load_room("nonexistent")


def test_room_store_creates_directory(tmp_path):
    """Test RoomStore creates base_dir automatically."""
    new_dir = tmp_path / "new_rooms"
    assert not new_dir.exists()
    
    store = RoomStore(new_dir)
    assert new_dir.exists()
