"""Unit tests for readstate.py."""

import json
import pytest
from pathlib import Path

from proxion_messenger_core.readstate import ReadState


def test_readstate_mark_and_last_read():
    """Test mark_read and last_read."""
    state = ReadState()
    
    state.mark_read("thread-1", "msg-123")
    assert state.last_read("thread-1") == "msg-123"
    
    state.mark_read("thread-1", "msg-456")
    assert state.last_read("thread-1") == "msg-456"


def test_readstate_last_read_nonexistent():
    """Test last_read returns None for unset thread."""
    state = ReadState()
    assert state.last_read("nonexistent") is None


def test_readstate_save_and_load(tmp_path):
    """Test save and load round-trip."""
    state = ReadState()
    state.mark_read("thread-1", "msg-100")
    state.mark_read("thread-2", "msg-200")
    
    path = tmp_path / "readstate.json"
    state.save(path)
    
    loaded = ReadState.load(path)
    assert loaded.last_read("thread-1") == "msg-100"
    assert loaded.last_read("thread-2") == "msg-200"


def test_readstate_load_nonexistent(tmp_path):
    """Test load returns empty state for nonexistent file."""
    path = tmp_path / "nonexistent.json"
    state = ReadState.load(path)
    
    assert state.last_read("anything") is None
    assert len(state._marks) == 0


def test_readstate_load_empty_file(tmp_path):
    """Test load handles empty JSON file."""
    path = tmp_path / "empty.json"
    path.write_text("{}")
    
    state = ReadState.load(path)
    assert len(state._marks) == 0


def test_readstate_save_creates_directory(tmp_path):
    """Test save creates parent directory if needed."""
    nested_dir = tmp_path / "nested" / "path"
    path = nested_dir / "readstate.json"
    
    state = ReadState()
    state.mark_read("thread", "msg")
    state.save(path)
    
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["marks"] == {"thread": "msg"}
