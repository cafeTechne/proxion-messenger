"""Tests for schema version 37 migration (R15)."""
import sqlite3
import tempfile
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    return LocalStore(path)


class TestSchemaVersion37:
    def test_schema_version_bumped_to_37(self):
        assert LocalStore._SCHEMA_VERSION >= 37

    def test_peer_attestations_table_exists(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='peer_attestations'"
        ).fetchone()
        conn.close()
        assert row is not None, "peer_attestations table must exist"

    def test_policy_tier_transitions_table_exists(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='policy_tier_transitions'"
        ).fetchone()
        conn.close()
        assert row is not None, "policy_tier_transitions table must exist"

    def test_operation_budget_scopes_table_exists(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='operation_budget_scopes'"
        ).fetchone()
        conn.close()
        assert row is not None, "operation_budget_scopes table must exist"

    def test_event_stream_cursors_table_exists(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='event_stream_cursors'"
        ).fetchone()
        conn.close()
        assert row is not None, "event_stream_cursors table must exist"

    def test_peer_attestation_crud(self, store):
        import json, time
        attest = {"peer_did": "did:key:abc", "gateway_url": "https://gw.example", "expires_at": time.time() + 3600}
        store.save_peer_attestation(
            peer_did="did:key:abc",
            attestation_json=json.dumps(attest),
            attestation_hash="deadbeef",
            expires_at=attest["expires_at"],
            verified=True,
        )
        result = store.get_peer_attestation("did:key:abc")
        assert result is not None
        assert result["attestation_hash"] == "deadbeef"
        assert result["verified"] == 1

    def test_peer_attestation_expires_index_exists(self, store):
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_peer_attestations_expires'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_policy_tier_transition_crud(self, store):
        store.save_policy_tier_transition(
            transition_id="txn-001",
            from_tier="normal",
            to_tier="elevated",
            trigger_type="auto",
            trigger_detail="auth_lockouts=5",
            actor_webid="",
        )
        transitions = store.get_recent_policy_tier_transitions(limit=10)
        assert len(transitions) >= 1
        assert transitions[0]["from_tier"] == "normal"
        assert transitions[0]["to_tier"] == "elevated"
