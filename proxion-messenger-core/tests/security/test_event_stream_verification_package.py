"""Tests for event stream verification packages (R16)."""
import time
import pytest
from proxion_messenger_core.event_stream import build_verification_package


def _make_events(n: int, start_seq: int = 1):
    events = []
    for i in range(n):
        seq = start_seq + i
        prev = events[-1]["event_hash"] if events else ""
        ev = {
            "event_id": f"ev-{seq:04d}",
            "stream_sequence": seq,
            "event_hash": f"hash{seq:04d}",
            "prev_hash": prev,
            "event_type": "test_event",
            "severity": "info",
            "created_at": time.time(),
            "details": "",
        }
        events.append(ev)
    return events


def test_package_has_sequence_window_and_hash():
    events = _make_events(5)
    pkg = build_verification_package(events, consumer_id="auditor-1")
    assert "sequence_window" in pkg
    # sequence_window is [min, max]
    assert pkg["sequence_window"][0] == 1
    assert pkg["sequence_window"][1] == 5
    assert "package_hash" in pkg
    assert len(pkg["package_hash"]) == 64  # SHA-256 hex


def test_package_hash_matches_contents():
    """package_hash must equal SHA-256 of canonical JSON of the other fields."""
    import hashlib
    import json
    events = _make_events(3)
    pkg = build_verification_package(events, consumer_id="auditor-1")
    payload = {k: v for k, v in pkg.items() if k != "package_hash"}
    canonical = json.dumps(payload, sort_keys=True, default=str).encode()
    expected = hashlib.sha256(canonical).hexdigest()
    assert pkg["package_hash"] == expected


def test_package_detects_gaps():
    events = _make_events(4)
    # Remove the middle event to create a sequence gap: [1, 3, 4]
    events_with_gap = [events[0], events[2], events[3]]
    pkg = build_verification_package(events_with_gap, consumer_id="auditor-1")
    assert pkg["has_gap"] is True
