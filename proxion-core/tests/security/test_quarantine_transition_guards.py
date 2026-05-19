"""R11: Federation quarantine transition guard tests."""
import time
import uuid
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _add_item(store):
    item_id = str(uuid.uuid4())
    store.add_quarantine_item(
        id=item_id,
        item_type="relay",
        source_identity="did:key:zTest",
        payload_json='{"msg":"test"}',
        reason="test",
        created_at=time.time(),
    )
    return item_id


def test_quarantine_release_idempotent(store):
    item_id = _add_item(store)
    first = store.release_quarantine_item(item_id)
    second = store.release_quarantine_item(item_id)
    assert first is True
    assert second is False  # already released


def test_quarantine_drop_idempotent(store):
    item_id = _add_item(store)
    first = store.drop_quarantine_item(item_id)
    second = store.drop_quarantine_item(item_id)
    assert first is True
    assert second is False


def test_invalid_status_transition_rejected(store):
    item_id = _add_item(store)
    # Release first
    store.release_quarantine_item(item_id)
    # Now try to drop an already-released item — invalid transition
    with pytest.raises(ValueError, match="invalid quarantine transition"):
        store.transition_quarantine_item(item_id, "drop")


def test_pending_item_can_be_released_via_transition(store):
    item_id = _add_item(store)
    new_status = store.transition_quarantine_item(item_id, "release")
    assert new_status == "released"


def test_pending_item_can_be_dropped_via_transition(store):
    item_id = _add_item(store)
    new_status = store.transition_quarantine_item(item_id, "drop")
    assert new_status == "dropped"


def test_transition_unknown_item_raises(store):
    with pytest.raises(ValueError, match="not found"):
        store.transition_quarantine_item("nonexistent-id", "release")


def test_quarantine_status_helper_maps_correctly(store):
    item_id = _add_item(store)
    item = store.get_quarantine_item(item_id)
    assert LocalStore._quarantine_status(item) == "pending"
    store.release_quarantine_item(item_id)
    item = store.get_quarantine_item(item_id)
    assert LocalStore._quarantine_status(item) == "released"
