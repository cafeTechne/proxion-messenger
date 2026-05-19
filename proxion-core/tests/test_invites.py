"""Tests for room invitation codes."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from proxion_messenger_core.invites import (
    InviteRecord, create_invite, get_invite, use_invite, revoke_invite, list_invites
)


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
async def test_create_invite_generates_code(mock_stash):
    """create_invite should generate a unique code and persist it."""
    room_id = "room-123"
    webid = "https://alice.example/profile#me"
    
    rec = await create_invite(mock_stash, room_id, webid)
    
    assert rec.code
    assert len(rec.code) > 10  # token_urlsafe should be substantial
    assert rec.room_id == room_id
    assert rec.created_by_webid == webid
    assert rec.active is True
    assert rec.use_count == 0
    mock_stash.put.assert_called_once()


@pytest.mark.asyncio
async def test_create_invite_with_custom_expiry(mock_stash):
    """create_invite should respect expires_hours parameter."""
    room_id = "room-456"
    webid = "https://bob.example/profile#me"
    
    rec = await create_invite(mock_stash, room_id, webid, expires_hours=48)
    
    expires_dt = datetime.fromisoformat(rec.expires_iso)
    created_dt = datetime.fromisoformat(rec.created_iso)
    diff = expires_dt - created_dt
    
    # Should be close to 48 hours
    assert timedelta(hours=47) < diff < timedelta(hours=49)


@pytest.mark.asyncio
async def test_get_invite_returns_record(mock_stash):
    """get_invite should retrieve and parse a stored invite."""
    code = "abc123def456"
    rec_dict = {
        "code": code,
        "room_id": "room-789",
        "created_by_webid": "https://alice.example/profile#me",
        "created_iso": "2026-04-12T10:00:00+00:00",
        "expires_iso": "2026-04-13T10:00:00+00:00",
        "max_uses": 0,
        "use_count": 0,
        "active": True,
    }
    mock_stash.get.return_value = json.dumps(rec_dict).encode()
    
    rec = await get_invite(mock_stash, code)
    
    assert rec is not None
    assert rec.code == code
    assert rec.room_id == "room-789"
    mock_stash.get.assert_called_once_with(f"invites/{code}.json")


@pytest.mark.asyncio
async def test_get_invite_returns_none_if_not_found(mock_stash):
    """get_invite should return None if code not found."""
    mock_stash.get.return_value = None
    
    rec = await get_invite(mock_stash, "nonexistent")
    
    assert rec is None


@pytest.mark.asyncio
async def test_use_invite_increments_count(mock_stash):
    """use_invite should increment use_count and persist."""
    from datetime import datetime, timedelta, timezone
    
    code = "abc123def456"
    # Use a future expiry time
    future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    rec_dict = {
        "code": code,
        "room_id": "room-101",
        "created_by_webid": "https://alice.example/profile#me",
        "created_iso": "2026-04-12T10:00:00+00:00",
        "expires_iso": future_time,
        "max_uses": 0,
        "use_count": 0,
        "active": True,
    }
    
    async def get_impl(key):
        return json.dumps(rec_dict).encode()
    
    mock_stash.get.side_effect = get_impl
    
    rec = await use_invite(mock_stash, code)
    
    assert rec is not None
    assert rec.use_count == 1
    mock_stash.put.assert_called_once()


@pytest.mark.asyncio
async def test_use_invite_respects_max_uses(mock_stash):
    """use_invite should reject if max_uses exceeded."""
    from datetime import datetime, timedelta, timezone
    
    code = "abc123def456"
    # Use a future expiry time
    future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    rec_dict = {
        "code": code,
        "room_id": "room-202",
        "created_by_webid": "https://alice.example/profile#me",
        "created_iso": "2026-04-12T10:00:00+00:00",
        "expires_iso": future_time,
        "max_uses": 2,
        "use_count": 2,
        "active": True,
    }
    mock_stash.get.return_value = json.dumps(rec_dict).encode()
    
    rec = await use_invite(mock_stash, code)
    
    assert rec is None


@pytest.mark.asyncio
async def test_use_invite_rejects_expired(mock_stash):
    """use_invite should reject if expired."""
    code = "abc123def456"
    past_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    rec_dict = {
        "code": code,
        "room_id": "room-303",
        "created_by_webid": "https://alice.example/profile#me",
        "created_iso": "2026-04-11T10:00:00+00:00",
        "expires_iso": past_time,
        "max_uses": 0,
        "use_count": 0,
        "active": True,
    }
    mock_stash.get.return_value = json.dumps(rec_dict).encode()
    
    rec = await use_invite(mock_stash, code)
    
    assert rec is None


@pytest.mark.asyncio
async def test_revoke_invite_marks_inactive(mock_stash):
    """revoke_invite should mark the invite as inactive."""
    code = "abc123def456"
    rec_dict = {
        "code": code,
        "room_id": "room-404",
        "created_by_webid": "https://alice.example/profile#me",
        "created_iso": "2026-04-12T10:00:00+00:00",
        "expires_iso": "2026-04-13T10:00:00+00:00",
        "max_uses": 0,
        "use_count": 0,
        "active": True,
    }
    mock_stash.get.return_value = json.dumps(rec_dict).encode()
    
    success = await revoke_invite(mock_stash, code)
    
    assert success is True
    mock_stash.put.assert_called_once()


@pytest.mark.asyncio
async def test_list_invites_filters_by_room(mock_stash):
    """list_invites should filter by room_id when provided."""
    invite1_dict = {
        "code": "code1",
        "room_id": "room-500",
        "created_by_webid": "https://alice.example/profile#me",
        "created_iso": "2026-04-12T10:00:00+00:00",
        "expires_iso": "2026-04-13T10:00:00+00:00",
        "max_uses": 0,
        "use_count": 0,
        "active": True,
    }
    invite2_dict = {
        "code": "code2",
        "room_id": "room-501",
        "created_by_webid": "https://alice.example/profile#me",
        "created_iso": "2026-04-12T10:00:00+00:00",
        "expires_iso": "2026-04-13T10:00:00+00:00",
        "max_uses": 0,
        "use_count": 0,
        "active": True,
    }
    
    mock_stash.list.return_value = ["invites/code1.json", "invites/code2.json"]
    
    call_count = 0
    def get_impl(key):
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            return json.dumps(invite1_dict).encode()
        else:
            return json.dumps(invite2_dict).encode()
    
    mock_stash.get.side_effect = get_impl
    
    invites = await list_invites(mock_stash, room_id="room-500")
    
    assert len(invites) == 1
    assert invites[0].room_id == "room-500"
