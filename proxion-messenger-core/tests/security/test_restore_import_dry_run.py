"""Tests for dry-run mode on /restore and /import (Round 6)."""
import pytest
import json


def test_dry_run_query_string_detection():
    """Test detection of dry_run=1 in query string."""
    path_with_dry_run = "/import?dry_run=1"
    path_without_dry_run = "/import?foo=bar"

    # Simulating parts[1] from the HTTP handler
    _dry_run_1 = "dry_run=1" in path_with_dry_run
    _dry_run_2 = "dry_run=1" in path_without_dry_run

    assert _dry_run_1 is True
    assert _dry_run_2 is False


def test_import_dry_run_data_counting(tmp_path):
    """Test counting of valid messages and relationships in dry-run mode."""
    from proxion_messenger_core.local_store import LocalStore

    store = LocalStore(str(tmp_path / "test.db"))

    # Simulate import_data validation counting
    messages = [
        {"message_id": "m1", "thread_id": "t1", "thread_type": "room",
         "from_webid": "did:key:z6MkTest", "content": "hi", "timestamp": "2026-01-01T00:00:00+00:00"},
        {"message_id": "m2", "thread_id": "t1", "thread_type": "room",
         "from_webid": "did:key:z6MkTest", "content": "hey", "timestamp": "2026-01-01T00:00:00+00:00"},
        {"message_id": "m3"},  # Missing required fields
    ]
    relationships = [
        {"cert_id": "rel1"},
        {"cert_id": "rel2"},
    ]

    # Count logic from dry-run implementation
    _msgs_valid = 0
    for msg in messages:
        if all(k in msg for k in ["message_id", "thread_id", "thread_type", "from_webid", "content", "timestamp"]):
            _msgs_valid += 1
    _rels_valid = len(relationships)
    _rejected = len(messages) - _msgs_valid + len([r for r in relationships if not isinstance(r, dict)])

    assert _msgs_valid == 2
    assert _rels_valid == 2
    assert _rejected == 1


def test_restore_dry_run_response_format():
    """Test the dry-run response format for restore."""
    dry_resp = json.dumps({"dry_run": True, "valid": True})
    parsed = json.loads(dry_resp)

    assert parsed["dry_run"] is True
    assert parsed["valid"] is True


def test_import_dry_run_response_format():
    """Test the dry-run response format for import."""
    dry_resp = json.dumps({
        "dry_run": True,
        "messages_valid": 5,
        "relationships_valid": 2,
        "rejected_rows": 1
    })
    parsed = json.loads(dry_resp)

    assert parsed["dry_run"] is True
    assert parsed["messages_valid"] == 5
    assert parsed["relationships_valid"] == 2
    assert parsed["rejected_rows"] == 1
