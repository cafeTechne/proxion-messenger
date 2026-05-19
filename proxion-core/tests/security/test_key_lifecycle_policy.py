"""Tests for key lifecycle policy enforcement (R16)."""
import time
import pytest
from proxion_messenger_core.key_lifecycle_policy import (
    evaluate_key_lifecycle,
    apply_key_lifecycle_escalation,
)


@pytest.fixture
def store(tmp_path):
    from proxion_messenger_core.local_store import LocalStore
    return LocalStore(str(tmp_path / "test.db"))


def test_warns_on_overdue_rotation():
    now = time.time()
    # Identity key created 100 days ago (default max is 90d)
    old_identity = now - (100 * 86400)
    result = evaluate_key_lifecycle(
        identity_key_created_at=old_identity,
        store_key_created_at=now,
    )
    assert not result["ok"]
    assert len(result["warnings"]) > 0 or len(result["violations"]) > 0


def test_violation_triggers_escalation(store):
    from proxion_messenger_core.security_policy import get_policy, reload_policy, TIER_NORMAL
    reload_policy()
    assert get_policy().get_tier() == TIER_NORMAL

    now = time.time()
    # Identity key created 200 days ago — well past grace window
    very_old = now - (200 * 86400)
    result = evaluate_key_lifecycle(
        identity_key_created_at=very_old,
        store_key_created_at=now,
    )
    import os
    os.environ["PROXION_KEY_LIFECYCLE_ESCALATION"] = "restrictive"
    try:
        apply_key_lifecycle_escalation(result, store=store)
        from proxion_messenger_core.security_policy import TIER_RESTRICTIVE
        assert get_policy().get_tier() >= TIER_RESTRICTIVE
    finally:
        del os.environ["PROXION_KEY_LIFECYCLE_ESCALATION"]
    reload_policy()


def test_legacy_state_backfills_timestamps(tmp_path):
    """AgentState.load() backfills created_at timestamps for legacy state files."""
    import os
    from proxion_messenger_core.persist import AgentState

    passphrase = b"test-passphrase-ok"
    state_path = tmp_path / "agent.json"

    # Generate and save a fresh state (which will include created_at)
    state = AgentState.generate()
    state.save(str(state_path), passphrase)

    # Patch out the timestamps to simulate legacy state
    import json
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    raw.pop("identity_key_created_at", None)
    raw.pop("store_key_created_at", None)
    state_path.write_text(json.dumps(raw), encoding="utf-8")

    # Load should backfill
    before = time.time()
    loaded = AgentState.load(str(state_path), passphrase)
    after = time.time()

    assert loaded.identity_key_created_at is not None
    assert loaded.store_key_created_at is not None
    assert before <= loaded.identity_key_created_at <= after
