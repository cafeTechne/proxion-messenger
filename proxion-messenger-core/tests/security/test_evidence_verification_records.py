"""Tests for evidence_verification_records store helpers."""
import uuid
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_save_and_list_records(store):
    store.save_evidence_verification_record(
        record_id=str(uuid.uuid4()),
        evidence_type="event_chain",
        evidence_id="evt-001",
        verifier="auditor-a",
        status="passed",
    )
    records = store.list_evidence_verification_records()
    assert len(records) == 1
    assert records[0]["evidence_type"] == "event_chain"
    assert records[0]["status"] == "passed"


def test_failed_record_stores_detail(store):
    store.save_evidence_verification_record(
        record_id=str(uuid.uuid4()),
        evidence_type="policy_hash",
        evidence_id="pol-001",
        verifier="auditor-b",
        status="failed",
        detail="hash_mismatch: expected=abc actual=def",
    )
    records = store.list_evidence_verification_records(evidence_type="policy_hash")
    assert len(records) == 1
    assert records[0]["status"] == "failed"
    assert "hash_mismatch" in records[0]["detail"]


def test_records_filtered_by_evidence_type(store):
    for i in range(3):
        store.save_evidence_verification_record(
            record_id=str(uuid.uuid4()),
            evidence_type="slo_snapshot",
            evidence_id=f"slo-{i}",
            verifier="auto",
            status="passed",
        )
    store.save_evidence_verification_record(
        record_id=str(uuid.uuid4()),
        evidence_type="policy_hash",
        evidence_id="pol-99",
        verifier="auto",
        status="passed",
    )
    slo_records = store.list_evidence_verification_records(evidence_type="slo_snapshot")
    assert len(slo_records) == 3
    all_records = store.list_evidence_verification_records()
    assert len(all_records) == 4
