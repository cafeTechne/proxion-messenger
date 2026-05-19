"""Tests for proxion_messenger_core.export."""
from __future__ import annotations

import json
import os
import pytest
from unittest.mock import MagicMock, patch

from proxion_messenger_core.export import (
    export_thread_to_json,
    export_thread_to_markdown,
)
from proxion_messenger_core.messaging import Message


def _make_message(message_id, content, timestamp=1700000000):
    return Message(
        message_id=message_id,
        cert_id="cert-001",
        from_pub_hex="aabbcc",
        content=content,
        timestamp=timestamp,
        signature="sig",
    )


@pytest.fixture
def fake_cert():
    cert = MagicMock()
    cert.certificate_id = "cert-001"
    return cert


@pytest.fixture
def fake_pod():
    return MagicMock()


def test_export_thread_to_json_produces_valid_json(tmp_path, fake_cert, fake_pod):
    messages = [
        _make_message("m1", "Hello"),
        _make_message("m2", "World"),
    ]

    with patch("proxion_messenger_core.export.receive", return_value=messages):
        out = str(tmp_path / "export.json")
        count = export_thread_to_json(fake_cert, fake_pod, out)

    assert count == 2
    with open(out, encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["message_id"] == "m1"
    assert data[1]["content"] == "World"


def test_export_thread_to_markdown_includes_display_names(tmp_path, fake_cert, fake_pod):
    messages = [_make_message("m1", "Hey there", timestamp=1700000000)]

    with patch("proxion_messenger_core.export.receive", return_value=messages):
        out = str(tmp_path / "export.md")
        count = export_thread_to_markdown(
            fake_cert, fake_pod, out,
            display_names={"aabbcc": "Alice"},
        )

    assert count == 1
    text = open(out, encoding="utf-8").read()
    assert "Alice" in text
    assert "Hey there" in text


def test_export_thread_to_json_applies_edits(tmp_path, fake_cert, fake_pod):
    original = _make_message("m1", "Old content", timestamp=100)
    edit = Message(
        message_id="m2",
        cert_id="cert-001",
        from_pub_hex="aabbcc",
        content="New content",
        timestamp=200,
        signature="sig",
        reply_to_id="m1",
        message_type="edit",
    )

    with patch("proxion_messenger_core.export.receive", return_value=[original, edit]):
        out = str(tmp_path / "export.json")
        count = export_thread_to_json(fake_cert, fake_pod, out)

    # apply_edits removes the edit record; only the updated original remains
    assert count == 1
    with open(out, encoding="utf-8") as f:
        data = json.load(f)
    assert data[0]["content"] == "New content"


def test_export_thread_to_json_empty_thread(tmp_path, fake_cert, fake_pod):
    with patch("proxion_messenger_core.export.receive", return_value=[]):
        out = str(tmp_path / "empty.json")
        count = export_thread_to_json(fake_cert, fake_pod, out)

    assert count == 0
    with open(out, encoding="utf-8") as f:
        data = json.load(f)
    assert data == []
