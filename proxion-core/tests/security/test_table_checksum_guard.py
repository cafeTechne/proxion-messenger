"""R9: Table checksum tamper detection tests."""
import sqlite3
import time
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_checksum_snapshot_and_verify_match(store):
    tables = ["audit_logs", "security_events"]
    store.snapshot_security_checksums(tables)
    mismatches = store.verify_security_checksums(tables)
    assert mismatches == []


def test_compute_table_checksum_returns_dict(store):
    result = store.compute_table_checksum("audit_logs")
    assert "checksum" in result
    assert "row_count" in result
    assert "table_name" in result
    assert result["table_name"] == "audit_logs"


def test_checksum_changes_after_insert(store):
    store.snapshot_security_checksums(["security_events"])
    before = store.compute_table_checksum("security_events")["checksum"]
    store.save_security_event("test_event", "info", details="tamper test")
    after = store.compute_table_checksum("security_events")["checksum"]
    assert before != after


def test_checksum_mismatch_detected_after_insert(store):
    tables = ["security_events"]
    store.snapshot_security_checksums(tables)
    # Insert data to cause mismatch
    store.save_security_event("checksum_test", "info", details="mismatch test")
    mismatches = store.verify_security_checksums(tables)
    assert len(mismatches) == 1
    assert mismatches[0]["table"] == "security_events"


def test_verify_returns_empty_when_no_baseline(store):
    # No snapshot taken yet — verify should skip (no baseline)
    mismatches = store.verify_security_checksums(["relationships"])
    assert mismatches == []


def test_snapshot_overwrites_previous_baseline(store):
    tables = ["audit_logs"]
    store.snapshot_security_checksums(tables)
    # Add a security event entry to shift the checksum
    store.save_security_event("test_action", "info", details="baseline shift")
    # Take a new snapshot (new baseline)
    store.snapshot_security_checksums(tables)
    # Verify should now pass with the new baseline
    mismatches = store.verify_security_checksums(tables)
    assert mismatches == []


def test_mismatch_contains_expected_and_actual_checksums(store):
    tables = ["security_events"]
    store.snapshot_security_checksums(tables)
    store.save_security_event("mismatch_probe", "info")
    mismatches = store.verify_security_checksums(tables)
    assert len(mismatches) == 1
    m = mismatches[0]
    assert "expected_checksum" in m
    assert "actual_checksum" in m
    assert m["expected_checksum"] != m["actual_checksum"]
