"""Tests for peer registry (peerdb module)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from proxion_messenger_core.peerdb import (
    PeerRecord,
    register_peer,
    get_peer,
    list_peers,
    remove_peer,
    touch_peer,
    _did_key,
)


@pytest.fixture
def mock_stash():
    """Fixture for mocked StashClient."""
    stash = AsyncMock()
    stash.put = AsyncMock()
    stash.get = AsyncMock()
    stash.delete = AsyncMock()
    stash.list = AsyncMock(return_value=[])
    return stash


@pytest.mark.asyncio
async def test_register_peer_stores_record(mock_stash):
    """Test that register_peer creates and persists a PeerRecord."""
    did = "did:key:z6MkhaXgBZDvotzL"
    pod_url = "https://alice.pod"
    display_name = "Alice"

    rec = await register_peer(mock_stash, did, pod_url, display_name)

    assert rec.did == did
    assert rec.pod_url == pod_url
    assert rec.display_name == display_name
    assert rec.trusted is False
    assert rec.last_seen_iso

    # Verify stash.put was called
    mock_stash.put.assert_called_once()
    args = mock_stash.put.call_args
    assert args[0][0] == _did_key(did)


@pytest.mark.asyncio
async def test_register_peer_updates_existing(mock_stash):
    """Test that register_peer overwrites existing records."""
    did = "did:key:z6MkhaXgBZDvotzL"
    
    # First registration
    rec1 = await register_peer(mock_stash, did, "https://alice1.pod", "Alice")
    
    # Update
    rec2 = await register_peer(mock_stash, did, "https://alice2.pod", "Alice Updated", trusted=True)

    assert rec2.pod_url == "https://alice2.pod"
    assert rec2.display_name == "Alice Updated"
    assert rec2.trusted is True


@pytest.mark.asyncio
async def test_get_peer_returns_record(mock_stash):
    """Test that get_peer retrieves an existing record."""
    did = "did:key:z6MkhaXgBZDvotzL"
    rec_dict = {
        "did": did,
        "pod_url": "https://alice.pod",
        "display_name": "Alice",
        "last_seen_iso": datetime.now(timezone.utc).isoformat(),
        "trusted": False,
    }

    mock_stash.get.return_value = json.dumps(rec_dict).encode()

    rec = await get_peer(mock_stash, did)

    assert rec is not None
    assert rec.did == did
    assert rec.pod_url == "https://alice.pod"
    mock_stash.get.assert_called_once_with(_did_key(did))


@pytest.mark.asyncio
async def test_get_peer_not_found_returns_none(mock_stash):
    """Test that get_peer returns None for nonexistent peers."""
    mock_stash.get.side_effect = Exception("Not found")

    rec = await get_peer(mock_stash, "did:key:missing")

    assert rec is None


@pytest.mark.asyncio
async def test_list_peers_all(mock_stash):
    """Test that list_peers returns all peer records."""
    now = datetime.now(timezone.utc).isoformat()
    
    rec1_dict = {
        "did": "did:key:z6Mk1",
        "pod_url": "https://alice.pod",
        "display_name": "Alice",
        "last_seen_iso": now,
        "trusted": False,
    }
    rec2_dict = {
        "did": "did:key:z6Mk2",
        "pod_url": "https://bob.pod",
        "display_name": "Bob",
        "last_seen_iso": now,
        "trusted": True,
    }

    mock_stash.list.return_value = ["peers/did_key_z6Mk1.json", "peers/did_key_z6Mk2.json"]
    mock_stash.get.side_effect = [
        json.dumps(rec1_dict).encode(),
        json.dumps(rec2_dict).encode(),
    ]

    records = await list_peers(mock_stash)

    assert len(records) == 2
    assert records[0].did == "did:key:z6Mk1"
    assert records[1].did == "did:key:z6Mk2"


@pytest.mark.asyncio
async def test_list_peers_trusted_only(mock_stash):
    """Test that list_peers filters by trusted status when requested."""
    now = datetime.now(timezone.utc).isoformat()
    
    rec1_dict = {
        "did": "did:key:z6Mk1",
        "pod_url": "https://alice.pod",
        "display_name": "Alice",
        "last_seen_iso": now,
        "trusted": False,
    }
    rec2_dict = {
        "did": "did:key:z6Mk2",
        "pod_url": "https://bob.pod",
        "display_name": "Bob",
        "last_seen_iso": now,
        "trusted": True,
    }

    mock_stash.list.return_value = ["peers/did_key_z6Mk1.json", "peers/did_key_z6Mk2.json"]
    mock_stash.get.side_effect = [
        json.dumps(rec1_dict).encode(),
        json.dumps(rec2_dict).encode(),
    ]

    records = await list_peers(mock_stash, trusted_only=True)

    assert len(records) == 1
    assert records[0].did == "did:key:z6Mk2"
    assert records[0].trusted is True


@pytest.mark.asyncio
async def test_remove_peer_deletes_record(mock_stash):
    """Test that remove_peer deletes the peer record."""
    did = "did:key:z6MkhaXgBZDvotzL"

    success = await remove_peer(mock_stash, did)

    assert success is True
    mock_stash.delete.assert_called_once_with(_did_key(did))


@pytest.mark.asyncio
async def test_touch_peer_updates_last_seen(mock_stash):
    """Test that touch_peer updates the last_seen_iso timestamp."""
    did = "did:key:z6MkhaXgBZDvotzL"
    old_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
    rec_dict = {
        "did": did,
        "pod_url": "https://alice.pod",
        "display_name": "Alice",
        "last_seen_iso": old_time,
        "trusted": False,
    }

    mock_stash.get.return_value = json.dumps(rec_dict).encode()

    rec = await touch_peer(mock_stash, did)

    assert rec is not None
    # last_seen_iso should be updated to now (approximately)
    assert rec.last_seen_iso > old_time
    
    # Verify stash.put was called to persist the update
    mock_stash.put.assert_called_once()
