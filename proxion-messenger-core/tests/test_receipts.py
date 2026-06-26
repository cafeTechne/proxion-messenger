"""Tests for read receipts module."""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock

from proxion_messenger_core.receipts import ReadReceipt, mark_message_read, get_read_receipts, has_been_read


@pytest.fixture
def mock_pod_client():
    """Mock SolidClient."""
    return MagicMock()


def test_mark_message_read_puts_json(mock_pod_client):
    """mark_message_read PUTs JSON with correct path and fields."""
    receipt = mark_message_read(
        mock_pod_client,
        message_id="msg123",
        thread_id="dm:cert456",
        reader_webid="bob@pod.com",
    )
    
    # Verify PUT was called
    mock_pod_client.put.assert_called_once()
    call_args = mock_pod_client.put.call_args
    
    # Check path
    path = call_args[0][0]
    assert path == "stash://receipts/dm:cert456/msg123.json"
    
    # Check JSON content
    content = call_args[0][1]
    data = json.loads(content.decode("utf-8"))
    assert data["message_id"] == "msg123"
    assert data["thread_id"] == "dm:cert456"
    assert data["reader_webid"] == "bob@pod.com"
    assert "read_at" in data


def test_get_read_receipts_returns_all_for_thread(mock_pod_client):
    """get_read_receipts returns all receipts for a thread."""
    receipt1_data = {
        "message_id": "msg1",
        "thread_id": "room:r1",
        "reader_webid": "alice@pod.com",
        "read_at": "2024-01-01T00:00:00+00:00",
    }
    receipt2_data = {
        "message_id": "msg2",
        "thread_id": "room:r1",
        "reader_webid": "bob@pod.com",
        "read_at": "2024-01-01T00:01:00+00:00",
    }
    receipt3_data = {
        "message_id": "msg3",
        "thread_id": "room:r1",
        "reader_webid": "alice@pod.com",
        "read_at": "2024-01-01T00:02:00+00:00",
    }
    
    mock_pod_client.list.return_value = [
        "stash://receipts/room:r1/msg1.json",
        "stash://receipts/room:r1/msg2.json",
        "stash://receipts/room:r1/msg3.json",
    ]
    mock_pod_client.get.side_effect = [
        json.dumps(receipt1_data).encode("utf-8"),
        json.dumps(receipt2_data).encode("utf-8"),
        json.dumps(receipt3_data).encode("utf-8"),
    ]
    
    receipts = get_read_receipts(mock_pod_client, "room:r1")
    
    assert len(receipts) == 3
    assert receipts[0].message_id == "msg1"
    assert receipts[1].message_id == "msg2"
    assert receipts[2].message_id == "msg3"


def test_get_read_receipts_filtered_by_message_id(mock_pod_client):
    """get_read_receipts filters by message_id."""
    receipt1_data = {
        "message_id": "msg1",
        "thread_id": "room:r1",
        "reader_webid": "alice@pod.com",
        "read_at": "2024-01-01T00:00:00+00:00",
    }
    receipt2_data = {
        "message_id": "msg2",
        "thread_id": "room:r1",
        "reader_webid": "bob@pod.com",
        "read_at": "2024-01-01T00:01:00+00:00",
    }
    
    mock_pod_client.list.return_value = [
        "stash://receipts/room:r1/msg1.json",
        "stash://receipts/room:r1/msg2.json",
    ]
    mock_pod_client.get.side_effect = [
        json.dumps(receipt1_data).encode("utf-8"),
        json.dumps(receipt2_data).encode("utf-8"),
    ]
    
    receipts = get_read_receipts(mock_pod_client, "room:r1", message_id="msg1")
    
    assert len(receipts) == 1
    assert receipts[0].message_id == "msg1"


def test_has_been_read_true_after_mark(mock_pod_client):
    """has_been_read returns True after marking as read."""
    receipt_data = {
        "message_id": "msg1",
        "thread_id": "dm:c1",
        "reader_webid": "alice@pod.com",
        "read_at": "2024-01-01T00:00:00+00:00",
    }
    
    mock_pod_client.list.return_value = [
        "stash://receipts/dm:c1/msg1.json",
    ]
    mock_pod_client.get.return_value = json.dumps(receipt_data).encode("utf-8")
    
    result = has_been_read(mock_pod_client, "dm:c1", "msg1")
    
    assert result is True


def test_has_been_read_false_when_no_receipt(mock_pod_client):
    """has_been_read returns False when no receipt exists."""
    mock_pod_client.list.return_value = []
    
    result = has_been_read(mock_pod_client, "dm:c1", "msg999")
    
    assert result is False
