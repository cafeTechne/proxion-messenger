"""Unit tests for inbox.py — unified message inbox."""

import pytest
import threading
import time
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from proxion_messenger_core.inbox import (
    InboxEntry, poll_inbox, watch_inbox,
)


@pytest.fixture
def mock_agent():
    """Mock AgentState."""
    agent = MagicMock()
    agent.signing_key_bytes = b"test-signing-key"
    return agent


@pytest.fixture
def mock_cert():
    """Mock RelationshipCertificate."""
    return MagicMock()


@pytest.fixture
def mock_pod_client():
    """Mock SolidClient."""
    return MagicMock()


@pytest.fixture
def mock_membership():
    """Mock RoomMembership."""
    membership = MagicMock()
    membership.cert = MagicMock()
    return membership


def test_inbox_entry_structure(mock_cert):
    """Test InboxEntry dataclass."""
    msg = MagicMock()
    
    entry = InboxEntry(source="dm", cert=mock_cert, message=msg)
    
    assert entry.source == "dm"
    assert entry.cert == mock_cert
    assert entry.message == msg


def test_poll_inbox_aggregates_dms(mock_agent, mock_cert, mock_pod_client):
    """Test poll_inbox() aggregates DM messages."""
    with patch("proxion_messenger_core.messaging.receive") as mock_receive:
        msg1 = MagicMock()
        msg1.timestamp = 1000
        msg1.message_id = "msg-1"

        msg2 = MagicMock()
        msg2.timestamp = 2000
        msg2.message_id = "msg-2"

        mock_receive.return_value = [msg1, msg2]

        entries = poll_inbox(
            agent=mock_agent,
            dm_clients=[(mock_cert, mock_pod_client)],
            room_memberships=[],
        )

        assert len(entries) == 2
        assert entries[0].source == "dm"
        assert entries[0].message == msg1


def test_poll_inbox_aggregates_rooms(mock_agent, mock_cert, mock_pod_client, mock_membership):
    """Test poll_inbox() aggregates room messages."""
    with patch("proxion_messenger_core.messaging.receive") as mock_receive, \
         patch("proxion_messenger_core.room.read_room") as mock_read_room:

        dm_msg = MagicMock()
        dm_msg.timestamp = 1000
        dm_msg.message_id = "dm-1"

        room_msg = MagicMock()
        room_msg.timestamp = 2000
        room_msg.message_id = "room-1"

        mock_receive.return_value = [dm_msg]
        mock_read_room.return_value = [room_msg]

        entries = poll_inbox(
            agent=mock_agent,
            dm_clients=[(mock_cert, mock_pod_client)],
            room_memberships=[(mock_membership, mock_pod_client)],
        )

        # Should have both DM and room message
        assert len(entries) == 2

        # DMs first (by timestamp), then room
        assert entries[0].source == "dm"
        assert entries[1].source == "room"


def test_poll_inbox_filters_by_since(mock_agent, mock_cert, mock_pod_client):
    """Test poll_inbox() respects 'since' timestamp."""
    with patch("proxion_messenger_core.messaging.receive") as mock_receive:
        old_msg = MagicMock()
        old_msg.timestamp = 1000  # before since

        new_msg = MagicMock()
        new_msg.timestamp = 3000  # after since

        mock_receive.return_value = [old_msg, new_msg]

        since = datetime.fromtimestamp(2000, tz=timezone.utc)

        entries = poll_inbox(
            agent=mock_agent,
            dm_clients=[(mock_cert, mock_pod_client)],
            room_memberships=[],
            since=since,
        )

        # Should only get new_msg (after since)
        assert len(entries) == 1
        assert entries[0].message == new_msg


def test_poll_inbox_sorts_by_timestamp(mock_agent, mock_cert, mock_pod_client):
    """Test poll_inbox() sorts entries by message timestamp."""
    with patch("proxion_messenger_core.messaging.receive") as mock_receive:
        msg3 = MagicMock()
        msg3.timestamp = 3000

        msg1 = MagicMock()
        msg1.timestamp = 1000

        msg2 = MagicMock()
        msg2.timestamp = 2000

        mock_receive.return_value = [msg3, msg1, msg2]

        entries = poll_inbox(
            agent=mock_agent,
            dm_clients=[(mock_cert, mock_pod_client)],
            room_memberships=[],
        )

        # Should be sorted chronologically
        assert entries[0].message == msg1
        assert entries[1].message == msg2
        assert entries[2].message == msg3


def test_watch_inbox_calls_callback(mock_agent, mock_cert, mock_pod_client):
    """Test watch_inbox() starts daemon thread and calls callback."""
    callback = MagicMock()

    with patch("proxion_messenger_core.inbox.poll_inbox") as mock_poll:
        msg = MagicMock()
        msg.timestamp = 1000
        msg.message_id = "msg-123"
        
        entry = InboxEntry(source="dm", cert=mock_cert, message=msg)
        
        # First call returns message, second returns empty to stop
        mock_poll.side_effect = [[entry], []]
        
        thread = watch_inbox(
            agent=mock_agent,
            dm_clients=[(mock_cert, mock_pod_client)],
            room_memberships=[],
            callback=callback,
            interval=0.05,  # Short interval for testing
        )
        
        assert isinstance(thread, threading.Thread)
        assert thread.daemon is True
        
        # Wait for thread to process one message
        time.sleep(0.5)
        
        # Callback should have been called for the entry
        assert callback.call_count >= 1


def test_watch_inbox_deduplicates_messages(mock_agent, mock_cert, mock_pod_client):
    """Test watch_inbox() doesn't call callback twice for same message."""
    callback = MagicMock()

    with patch("proxion_messenger_core.inbox.poll_inbox") as mock_poll:
        msg = MagicMock()
        msg.timestamp = 1000
        msg.message_id = "msg-123"
        
        entry = InboxEntry(source="dm", cert=mock_cert, message=msg)
        
        # Return same entry multiple times to test deduplication
        mock_poll.side_effect = [[entry], [entry], []]
        
        thread = watch_inbox(
            agent=mock_agent,
            dm_clients=[(mock_cert, mock_pod_client)],
            room_memberships=[],
            callback=callback,
            interval=0.05,
        )
        
        # Wait for thread to poll multiple times
        time.sleep(0.3)
        
        # Callback should only be called once (deduplicated)
        assert callback.call_count == 1


def test_watch_inbox_daemon_thread():
    """Test watch_inbox() returns daemon thread."""
    callback = MagicMock()
    
    with patch("proxion_messenger_core.inbox.poll_inbox"):
        mock_agent = MagicMock()
        mock_cert = MagicMock()
        mock_client = MagicMock()
        
        thread = watch_inbox(
            agent=mock_agent,
            dm_clients=[(mock_cert, mock_client)],
            room_memberships=[],
            callback=callback,
            interval=1.0,
        )
        
        # Thread should be daemon so it doesn't block program exit
        assert thread.daemon is True
