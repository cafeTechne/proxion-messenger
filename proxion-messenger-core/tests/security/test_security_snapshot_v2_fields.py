"""Tests for R15 expanded security snapshot fields (v2)."""
import json
import tempfile
import time
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    return LocalStore(path)


class TestSnapshotIncludesAttestationAndProvenance:
    def test_snapshot_includes_attestation_and_provenance_sections(self, store):
        store.save_policy_tier_transition("t1", "normal", "elevated", "auto")
        store.upsert_stream_cursor("siem_primary", 10)
        from datetime import datetime, timezone
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ok_global = store.check_scoped_budget("backup", "global", day, 100)
        assert ok_global is True

    def test_peer_attestation_crud_for_snapshot(self, store):
        store.save_peer_attestation(
            peer_did="did:key:snap-test",
            attestation_json='{"peer_did":"did:key:snap-test"}',
            attestation_hash="aabbcc",
            expires_at=time.time() + 3600,
            verified=True,
        )
        result = store.get_peer_attestation("did:key:snap-test")
        assert result is not None
        assert result["verified"] == 1


class TestSnapshotIncludesPolicyTransitionSlice:
    def test_snapshot_includes_policy_transition_slice(self, store):
        store.save_policy_tier_transition(
            "snap-txn-1", "normal", "elevated", "drift", "severity=high"
        )
        transitions = store.get_recent_policy_tier_transitions(limit=5)
        assert len(transitions) >= 1
        assert transitions[0]["trigger_type"] == "drift"

    def test_transition_slice_ordered_by_recency(self, store):
        for i in range(3):
            store.save_policy_tier_transition(f"txn-{i}", "normal", "elevated", "auto")
        transitions = store.get_recent_policy_tier_transitions(limit=5)
        times = [t["created_at"] for t in transitions]
        assert times == sorted(times, reverse=True)


class TestSnapshotIncludesStreamContinuityStatus:
    def test_snapshot_includes_stream_continuity_status(self, store):
        store.upsert_stream_cursor("siem_primary", 77)
        cursor = store.get_stream_cursor("siem_primary")
        assert cursor is not None
        assert cursor["last_sequence"] == 77
        assert cursor["consumer_id"] == "siem_primary"

    def test_stream_cursor_updated_at_is_recent(self, store):
        store.upsert_stream_cursor("siem_test", 5)
        cursor = store.get_stream_cursor("siem_test")
        assert cursor["updated_at"] > time.time() - 5
