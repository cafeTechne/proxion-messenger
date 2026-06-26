"""Tests for event stream sequence continuity (R15)."""
import tempfile
import time
import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.event_stream import stream_integrity_state, _STREAM_INTEGRITY_OK, _STREAM_INTEGRITY_GAP


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    return LocalStore(path)


class TestStreamSequenceMonotonicity:
    def test_stream_sequence_monotonicity_enforced(self, store):
        events = [
            {"id": "e1", "stream_sequence": 0},
            {"id": "e2", "stream_sequence": 1},
            {"id": "e3", "stream_sequence": 2},
        ]
        result = stream_integrity_state(store, "consumer-1", events)
        assert result["state"] == _STREAM_INTEGRITY_OK
        assert result["gap_at_sequence"] is None

    def test_stream_sequence_stored_after_processing(self, store):
        events = [{"id": "e1", "stream_sequence": 5}]
        stream_integrity_state(store, "consumer-seq", events)
        cursor = store.get_stream_cursor("consumer-seq")
        assert cursor is not None
        assert cursor["last_sequence"] == 5


class TestGapDetection:
    def test_gap_detection_sets_stream_integrity_warning(self, store):
        events = [
            {"id": "e1", "stream_sequence": 0},
            {"id": "e2", "stream_sequence": 5},
        ]
        result = stream_integrity_state(store, "consumer-gap", events)
        assert result["state"] == _STREAM_INTEGRITY_GAP
        assert result["gap_at_sequence"] == 5

    def test_no_gap_when_sequence_is_continuous(self, store):
        events = [{"id": f"e{i}", "stream_sequence": i} for i in range(10)]
        result = stream_integrity_state(store, "consumer-cont", events)
        assert result["state"] == _STREAM_INTEGRITY_OK


class TestCursorTableSupportsResume:
    def test_cursor_table_supports_resume_after_restart(self, store):
        store.upsert_stream_cursor("siem_primary", 42)
        cursor = store.get_stream_cursor("siem_primary")
        assert cursor is not None
        assert cursor["last_sequence"] == 42

    def test_cursor_upsert_updates_existing(self, store):
        store.upsert_stream_cursor("consumer-x", 10)
        store.upsert_stream_cursor("consumer-x", 20)
        cursor = store.get_stream_cursor("consumer-x")
        assert cursor["last_sequence"] == 20

    def test_missing_cursor_returns_none(self, store):
        cursor = store.get_stream_cursor("nonexistent-consumer")
        assert cursor is None
