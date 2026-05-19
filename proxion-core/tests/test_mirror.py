"""Tests for proxion_messenger_core.mirror."""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock

from proxion_messenger_core.mirror import mirror_room_to_pod, get_mirror_messages
from proxion_messenger_core.room import RoomConfig
from proxion_messenger_core.messaging import Message

@pytest.fixture
def mock_room():
    return RoomConfig(
        room_id="room-123",
        name="Test Room",
        owner_webid="owner",
        pod_url="http://pod",
        stash_root="stash://rooms/room-123/",
        created_at="2026-04-10T00:00:00Z"
    )

@pytest.fixture
def mock_message():
    return Message(
        message_id="msg-1",
        cert_id="room-123",
        from_pub_hex="alice-pub",
        content="hello",
        timestamp=1234567890,
        signature="sig-1",
        reply_to_id=None,
        message_type="text"
    )

def test_mirror_room_to_pod(mock_room, mock_message, monkeypatch):
    source_client = MagicMock()
    mirror_client = MagicMock()
    
    # Mock read_room to return our test message
    monkeypatch.setattr("proxion_messenger_core.room.read_room", lambda *args, **kwargs: [mock_message])
    
    mirror_room_to_pod(mock_room, source_client, mirror_client)
    
    # Verify metadata was written
    mirror_client.put.assert_any_call(
        "stash://mirrors/room-123/room.json",
        json.dumps({
            "room_id": "room-123",
            "name": "Test Room",
            "owner_webid": "owner",
            "pod_url": "http://pod",
        }).encode("utf-8")
    )
    
    # Verify message was written
    mirror_client.put.assert_any_call(
        "stash://mirrors/room-123/messages/msg-1.json",
        json.dumps({
            "message_id": "msg-1",
            "cert_id": "room-123",
            "from_pub_hex": "alice-pub",
            "content": "hello",
            "timestamp": 1234567890,
            "signature": "sig-1",
            "reply_to_id": None,
            "message_type": "text",
        }).encode("utf-8")
    )

def test_get_mirror_messages(mock_room, mock_message):
    mirror_client = MagicMock()
    mirror_client.list_resources.return_value = ["msg-1.json", "other.txt"]
    
    msg_data = {
        "message_id": "msg-1",
        "cert_id": "room-123",
        "from_pub_hex": "alice-pub",
        "content": "hello",
        "timestamp": 1234567890,
        "signature": "sig-1",
        "reply_to_id": None,
        "message_type": "text",
    }
    mirror_client.get.return_value = json.dumps(msg_data).encode("utf-8")
    
    msgs = get_mirror_messages("room-123", mirror_client)
    assert len(msgs) == 1
    assert msgs[0].message_id == "msg-1"
    assert msgs[0].content == "hello"

def test_mirror_since_filter_skips_old_messages(mock_room, monkeypatch):
    from proxion_messenger_core.messaging import Message
    
    msg1 = Message(message_id="msg-1", cert_id="room-123", from_pub_hex="alice", content="1", timestamp=10, signature="sig-1", reply_to_id=None, message_type="text")
    msg2 = Message(message_id="msg-2", cert_id="room-123", from_pub_hex="alice", content="2", timestamp=20, signature="sig-2", reply_to_id=None, message_type="text")
    msg3 = Message(message_id="msg-3", cert_id="room-123", from_pub_hex="alice", content="3", timestamp=30, signature="sig-3", reply_to_id=None, message_type="text")
    
    source_client = MagicMock()
    mirror_client = MagicMock()
    
    monkeypatch.setattr("proxion_messenger_core.room.read_room", lambda *args, **kwargs: [msg1, msg2, msg3])
    
    mirror_room_to_pod(mock_room, source_client, mirror_client, since=20)
    
    # 1 for room metadata, 2 for messages (msg-2, msg-3)
    assert mirror_client.put.call_count == 3
    
    calls = [call[0][0] for call in mirror_client.put.call_args_list]
    assert any("msg-2" in call for call in calls)
    assert any("msg-3" in call for call in calls)
    assert not any("msg-1" in call for call in calls)

def test_get_mirror_messages_empty_returns_empty_list():
    mirror_client = MagicMock()
    # Mock pod_client.list returning nothing
    mirror_client.list_resources.return_value = []
    
    msgs = get_mirror_messages("room-empty", mirror_client)
    assert msgs == []
