"""Tests for local notification queue."""

import json
from unittest.mock import AsyncMock

import pytest

from proxion_messenger_core.notifications import (
    NotificationRecord, notify, get_notifications, mark_notification_read, clear_notifications
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
async def test_notify_creates_record(mock_stash):
    """notify should create and persist a notification record."""
    event_type = "message"
    title = "New message"
    body = "Hello from Alice"
    data = {"room_id": "room-123"}
    
    rec = await notify(mock_stash, event_type, title, body, data)
    
    assert rec.id
    assert rec.event_type == event_type
    assert rec.title == title
    assert rec.body == body
    assert rec.data == data
    assert rec.read is False
    mock_stash.put.assert_called_once()


@pytest.mark.asyncio
async def test_notify_without_data(mock_stash):
    """notify should work with optional data field."""
    rec = await notify(mock_stash, "mention", "You were mentioned", "In room General")
    
    assert rec.data == {}
    mock_stash.put.assert_called_once()


@pytest.mark.asyncio
async def test_get_notifications_returns_all(mock_stash):
    """get_notifications should retrieve all notifications."""
    notif1 = {
        "id": "notif-1",
        "event_type": "message",
        "title": "New message",
        "body": "Hello",
        "data": {},
        "created_iso": "2026-04-13T10:00:00+00:00",
        "read": False,
    }
    notif2 = {
        "id": "notif-2",
        "event_type": "reaction",
        "title": "Reaction",
        "body": "👍",
        "data": {},
        "created_iso": "2026-04-13T11:00:00+00:00",
        "read": True,
    }
    
    mock_stash.list.return_value = ["notifications/notif-1.json", "notifications/notif-2.json"]
    
    call_count = 0
    def get_impl(key):
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            return json.dumps(notif1).encode()
        else:
            return json.dumps(notif2).encode()
    
    mock_stash.get.side_effect = get_impl
    
    notifs = await get_notifications(mock_stash)
    
    assert len(notifs) == 2


@pytest.mark.asyncio
async def test_get_notifications_unread_only(mock_stash):
    """get_notifications should filter to unread when requested."""
    notif1 = {
        "id": "notif-1",
        "event_type": "message",
        "title": "New message",
        "body": "Hello",
        "data": {},
        "created_iso": "2026-04-13T10:00:00+00:00",
        "read": False,
    }
    notif2 = {
        "id": "notif-2",
        "event_type": "reaction",
        "title": "Reaction",
        "body": "👍",
        "data": {},
        "created_iso": "2026-04-13T11:00:00+00:00",
        "read": True,
    }
    
    mock_stash.list.return_value = ["notifications/notif-1.json", "notifications/notif-2.json"]
    
    call_count = 0
    def get_impl(key):
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            return json.dumps(notif1).encode()
        else:
            return json.dumps(notif2).encode()
    
    mock_stash.get.side_effect = get_impl
    
    notifs = await get_notifications(mock_stash, unread_only=True)
    
    assert len(notifs) == 1
    assert notifs[0].read is False


@pytest.mark.asyncio
async def test_mark_notification_read_updates_record(mock_stash):
    """mark_notification_read should set read=True and persist."""
    notif_dict = {
        "id": "notif-1",
        "event_type": "message",
        "title": "New message",
        "body": "Hello",
        "data": {},
        "created_iso": "2026-04-13T10:00:00+00:00",
        "read": False,
    }
    
    mock_stash.get.return_value = json.dumps(notif_dict).encode()
    
    success = await mark_notification_read(mock_stash, "notif-1")
    
    assert success is True
    mock_stash.put.assert_called_once()
    
    # Verify the record was updated with read=True
    call_args = mock_stash.put.call_args
    updated_data = json.loads(call_args[0][1].decode())
    assert updated_data["read"] is True


@pytest.mark.asyncio
async def test_mark_notification_read_not_found(mock_stash):
    """mark_notification_read should return False if notification not found."""
    mock_stash.get.return_value = None
    
    success = await mark_notification_read(mock_stash, "nonexistent")
    
    assert success is False


@pytest.mark.asyncio
async def test_clear_notifications_deletes_read(mock_stash):
    """clear_notifications should delete read notifications by default."""
    notif1 = {
        "id": "notif-1",
        "event_type": "message",
        "title": "New message",
        "body": "Hello",
        "data": {},
        "created_iso": "2026-04-13T10:00:00+00:00",
        "read": True,
    }
    notif2 = {
        "id": "notif-2",
        "event_type": "reaction",
        "title": "Reaction",
        "body": "👍",
        "data": {},
        "created_iso": "2026-04-13T11:00:00+00:00",
        "read": False,
    }
    
    mock_stash.list.return_value = ["notifications/notif-1.json", "notifications/notif-2.json"]
    
    call_count = 0
    def get_impl(key):
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            return json.dumps(notif1).encode()
        else:
            return json.dumps(notif2).encode()
    
    mock_stash.get.side_effect = get_impl
    
    deleted_count = await clear_notifications(mock_stash, read_only=True)
    
    # Should only delete notif-1 (which is read=True)
    assert deleted_count == 1
    mock_stash.delete.assert_called_once()
