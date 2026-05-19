"""R11: Dual-control admin action workflow tests."""
import hashlib
import json
import time
import uuid
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _create(store, action_type="clear_retention_lock", expires_in=600):
    action_id = str(uuid.uuid4())
    store.create_pending_admin_action(
        action_id=action_id,
        action_type=action_type,
        payload_json=json.dumps({"lock_name": "audit_logs"}),
        requested_by="did:key:owner",
        expires_at=time.time() + expires_in,
    )
    return action_id


def test_critical_action_requires_request_then_confirm(store):
    action_id = _create(store)
    action = store.get_pending_admin_action(action_id)
    assert action is not None
    assert action["confirmed"] == 0
    challenge = hashlib.sha256(f"confirm:{action_id}".encode()).hexdigest()[:16]
    ok = store.confirm_admin_action(action_id, confirmed_by="did:key:owner")
    assert ok is True
    action = store.get_pending_admin_action(action_id)
    assert action["confirmed"] == 1


def test_unconfirmed_action_cannot_execute(store):
    action_id = _create(store)
    # Attempt to consume without confirming first
    consumed = store.consume_admin_action(action_id)
    assert consumed is False


def test_confirmed_action_single_use_enforced(store):
    action_id = _create(store)
    store.confirm_admin_action(action_id, confirmed_by="did:key:owner")
    # First consume succeeds
    first = store.consume_admin_action(action_id)
    assert first is True
    # Second consume must fail (already consumed)
    second = store.consume_admin_action(action_id)
    assert second is False


def test_expired_action_cannot_be_confirmed(store):
    action_id = _create(store, expires_in=-1)  # already expired
    ok = store.confirm_admin_action(action_id, confirmed_by="did:key:owner")
    assert ok is False


def test_action_metadata_stored_correctly(store):
    action_id = _create(store, action_type="set_security_tier")
    action = store.get_pending_admin_action(action_id)
    assert action["action_type"] == "set_security_tier"
    assert action["requested_by"] == "did:key:owner"
    assert action["consumed"] == 0
