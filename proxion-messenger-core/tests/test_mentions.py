"""Tests for mention parsing in gateway.py."""
from __future__ import annotations

import pytest
from proxion_messenger_core.gateway import extract_mentions

def test_extract_mentions_basic():
    known = {"alice": "webid-alice", "bob": "webid-bob"}
    content = "Hello @alice and @bob!"
    mentions = extract_mentions(content, known)
    assert set(mentions) == {"webid-alice", "webid-bob"}

def test_extract_mentions_unknown():
    known = {"alice": "webid-alice"}
    content = "Hello @charlie"
    mentions = extract_mentions(content, known)
    assert mentions == []

def test_extract_mentions_no_at():
    known = {"alice": "webid-alice"}
    content = "Hello alice"
    mentions = extract_mentions(content, known)
    assert mentions == []

def test_extract_mentions_duplicate():
    known = {"alice": "webid-alice"}
    content = "@alice @alice"
    mentions = extract_mentions(content, known)
    assert mentions == ["webid-alice"]

def test_extract_mentions_with_punctuation():
    known = {"alice": "webid-alice"}
    # The regex r"@(\w+)" handles trailing punctuation automatically if it's non-word
    content = "Hey @alice, how are you?"
    mentions = extract_mentions(content, known)
    assert mentions == ["webid-alice"]
