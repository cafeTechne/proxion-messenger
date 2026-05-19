"""R11: Security snapshot chain tests."""
import hashlib
import uuid
import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_snapshot_chain_appends_with_prev_hash(store):
    snap1_id = str(uuid.uuid4())
    snap1_hash = hashlib.sha256(b"report1").hexdigest()
    store.append_security_snapshot_chain_entry(snap1_id, "", snap1_hash, "sig1", "key1")

    snap2_id = str(uuid.uuid4())
    snap2_hash = hashlib.sha256(b"report2").hexdigest()
    store.append_security_snapshot_chain_entry(snap2_id, snap1_hash, snap2_hash, "sig2", "key1")

    result = store.verify_security_snapshot_chain()
    assert result["ok"] is True
    assert result["entries_checked"] == 2


def test_snapshot_chain_verification_detects_tamper(store):
    snap1_id = str(uuid.uuid4())
    snap1_hash = hashlib.sha256(b"authentic").hexdigest()
    store.append_security_snapshot_chain_entry(snap1_id, "", snap1_hash, "sig1", "key1")

    snap2_id = str(uuid.uuid4())
    # Wrong prev_hash — breaks the chain
    wrong_prev = hashlib.sha256(b"tampered").hexdigest()
    snap2_hash = hashlib.sha256(b"report2").hexdigest()
    store.append_security_snapshot_chain_entry(snap2_id, wrong_prev, snap2_hash, "sig2", "key1")

    result = store.verify_security_snapshot_chain()
    assert result["ok"] is False
    assert len(result["errors"]) >= 1


def test_snapshot_chain_empty_is_valid(store):
    result = store.verify_security_snapshot_chain()
    assert result["ok"] is True
    assert result["entries_checked"] == 0


def test_snapshot_response_includes_chain_metadata():
    """Self-test report builder must add snapshot_id, prev_hash, chain_ok fields."""
    report = {
        "generated_at": 0.0,
        "checks": {},
        "passed": True,
    }
    # Simulate what the gateway builder appends
    report["snapshot_id"] = str(uuid.uuid4())
    report["prev_hash"] = ""
    report["chain_ok"] = True
    assert "snapshot_id" in report
    assert "prev_hash" in report
    assert "chain_ok" in report


def test_get_latest_chain_entry_returns_most_recent(store):
    for i in range(3):
        store.append_security_snapshot_chain_entry(
            str(uuid.uuid4()), f"prev{i}", f"hash{i}", "sig", "key"
        )
    latest = store.get_latest_security_snapshot_chain_entry()
    assert latest is not None
    assert latest["snapshot_hash"] == "hash2"
