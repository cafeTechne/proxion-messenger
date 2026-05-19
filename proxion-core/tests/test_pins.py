"""Tests for proxion_messenger_core.pins."""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock

from proxion_messenger_core.pins import pin_message, unpin_message, get_pinned_messages, PinnedMessage
from proxion_messenger_core.messaging import Message


def _make_message(message_id="msg-abc", content="Hello, world!"):
    return Message(
        message_id=message_id,
        cert_id="cert-001",
        from_pub_hex="aabbcc",
        content=content,
        timestamp=1700000000,
        signature="sig",
    )


def _mock_pod():
    storage = {}

    client = MagicMock()
    client.put.side_effect = lambda path, data, **kw: storage.update({path: data})
    client.get.side_effect = lambda path: storage[path]
    client.list.side_effect = lambda path: [k for k in storage if k.startswith(path) and k != path]
    client.delete.side_effect = lambda path: storage.pop(path, None)
    return client, storage


def test_pin_message_stores_json():
    pod, storage = _mock_pod()
    msg = _make_message()
    pinned = pin_message(pod, msg, "dm:cert-001", "alice@example.com")

    assert isinstance(pinned, PinnedMessage)
    assert pinned.message_id == "msg-abc"
    assert pinned.thread_id == "dm:cert-001"
    assert pinned.pinned_by_webid == "alice@example.com"
    assert pinned.content_preview == "Hello, world!"

    # A JSON document was PUT to the expected path
    expected_path = "stash://pins/dm:cert-001/msg-abc.json"
    assert expected_path in storage
    doc = json.loads(storage[expected_path].decode("utf-8"))
    assert doc["message_id"] == "msg-abc"


def test_pin_message_truncates_preview():
    pod, _ = _mock_pod()
    long_content = "x" * 200
    msg = _make_message(content=long_content)
    pinned = pin_message(pod, msg, "room:room-1", "bob@example.com")
    assert len(pinned.content_preview) == 100


def test_unpin_message_removes_document():
    pod, storage = _mock_pod()
    msg = _make_message()
    pin_message(pod, msg, "dm:cert-001", "alice@example.com")
    assert len(storage) == 1

    unpin_message(pod, "msg-abc", "dm:cert-001")
    assert len(storage) == 0


def test_get_pinned_messages_returns_all():
    pod, _ = _mock_pod()
    pin_message(pod, _make_message("m1", "First"), "dm:c1", "alice@example.com")
    pin_message(pod, _make_message("m2", "Second"), "dm:c1", "alice@example.com")

    results = get_pinned_messages(pod, "dm:c1")
    assert len(results) == 2
    ids = {p.message_id for p in results}
    assert ids == {"m1", "m2"}


def test_get_pinned_messages_empty_thread():
    pod, _ = _mock_pod()
    # list() will raise SolidError for unknown path — simulate with empty list
    pod.list.side_effect = lambda path: []

    results = get_pinned_messages(pod, "dm:nonexistent")
    assert results == []
