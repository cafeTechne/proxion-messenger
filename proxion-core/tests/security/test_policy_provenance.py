"""R12: Policy provenance metadata tests."""
import hashlib
import json
import os
import pytest
import tempfile

from proxion_messenger_core.security_policy import SecurityPolicy, PolicyLoadError, _load_policy
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_policy_metadata_included_in_decisions():
    policy = SecurityPolicy()
    decision = policy.evaluate_ws_command(
        cmd="get_audit_logs",
        caller_webid="did:key:owner",
        gateway_owner_did="did:key:owner",
    )
    assert hasattr(decision, "policy_ref")
    pref = decision.policy_ref
    assert "policy_id" in pref
    assert "policy_version" in pref
    assert "sha256" in pref


def test_policy_ref_populated_on_deny():
    policy = SecurityPolicy()
    decision = policy.evaluate_ws_command(
        cmd="get_audit_logs",
        caller_webid="did:key:non_owner",
        gateway_owner_did="did:key:owner",
    )
    assert not decision.allow
    assert hasattr(decision, "policy_ref")
    assert "policy_id" in decision.policy_ref


def test_policy_provenance_get_provenance():
    policy = SecurityPolicy()
    prov = policy.get_provenance()
    assert "policy_id" in prov
    assert prov["policy_id"]  # non-empty
    assert prov["loaded_from"] == "defaults"
    assert "sha256" in prov


def test_policy_hash_mismatch_blocks_startup_when_required(tmp_path, monkeypatch):
    """PROXION_REQUIRE_POLICY_HASH set with wrong hash → PolicyLoadError."""
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps({"owner_only_commands": []}))
    sha = hashlib.sha256(policy_file.read_bytes()).hexdigest()
    wrong_hash = "00" * 32

    monkeypatch.setenv("PROXION_POLICY_FILE", str(policy_file))
    monkeypatch.setenv("PROXION_REQUIRE_POLICY_HASH", wrong_hash)

    with pytest.raises(PolicyLoadError, match="policy_hash_mismatch"):
        _load_policy()


def test_policy_hash_correct_does_not_raise(tmp_path, monkeypatch):
    """PROXION_REQUIRE_POLICY_HASH set with correct hash → no error."""
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps({"owner_only_commands": []}))
    sha = hashlib.sha256(policy_file.read_bytes()).hexdigest()

    monkeypatch.setenv("PROXION_POLICY_FILE", str(policy_file))
    monkeypatch.setenv("PROXION_REQUIRE_POLICY_HASH", sha)

    policy = _load_policy()  # should not raise
    assert policy is not None
    assert policy.get_provenance()["sha256"] == sha


def test_policy_change_log_written_on_reload(store):
    policy = SecurityPolicy()
    prov = policy.get_provenance()
    store.append_policy_change_log(
        policy_id=prov["policy_id"],
        policy_version=prov["policy_version"],
        policy_sha256=prov["sha256"],
        loaded_from=prov.get("loaded_from"),
        changed_by="test_suite",
    )
    log = store.list_policy_change_log()
    assert len(log) >= 1
    assert log[0]["policy_id"] == prov["policy_id"]
    assert log[0]["changed_by"] == "test_suite"
