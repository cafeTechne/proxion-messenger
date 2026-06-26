"""R9: Federation quarantine mode tests."""
import time
import uuid
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _add_item(store, item_type="relay", reason="quarantine_mode"):
    item_id = str(uuid.uuid4())
    store.add_quarantine_item(
        id=item_id,
        item_type=item_type,
        source_identity="did:key:sender",
        payload_json='{"test":"payload"}',
        reason=reason,
        created_at=time.time(),
    )
    return item_id


def test_quarantine_mode_captures_item(store):
    item_id = _add_item(store, item_type="relay")
    item = store.get_quarantine_item(item_id)
    assert item is not None
    assert item["item_type"] == "relay"
    assert item["released"] == 0
    assert item["dropped"] == 0


def test_list_quarantine_items_returns_pending(store):
    _add_item(store)
    _add_item(store)
    items = store.list_quarantine_items()
    assert len(items) >= 2


def test_release_quarantine_item_marks_released(store):
    item_id = _add_item(store)
    released = store.release_quarantine_item(item_id)
    assert released is True
    item = store.get_quarantine_item(item_id)
    assert item["released"] == 1
    assert item["dropped"] == 0


def test_drop_quarantine_item_marks_dropped(store):
    item_id = _add_item(store)
    dropped = store.drop_quarantine_item(item_id)
    assert dropped is True
    item = store.get_quarantine_item(item_id)
    assert item["dropped"] == 1


def test_released_item_excluded_from_list(store):
    id1 = _add_item(store, reason="test1")
    id2 = _add_item(store, reason="test2")
    store.release_quarantine_item(id1)
    items = store.list_quarantine_items()
    ids_in_list = {i["id"] for i in items}
    assert id1 not in ids_in_list
    assert id2 in ids_in_list


def test_dropped_item_excluded_from_list(store):
    id1 = _add_item(store)
    id2 = _add_item(store)
    store.drop_quarantine_item(id1)
    items = store.list_quarantine_items()
    ids_in_list = {i["id"] for i in items}
    assert id1 not in ids_in_list


def test_release_already_released_item_returns_false(store):
    item_id = _add_item(store)
    store.release_quarantine_item(item_id)
    second = store.release_quarantine_item(item_id)
    assert second is False


def test_drop_already_dropped_item_returns_false(store):
    item_id = _add_item(store)
    store.drop_quarantine_item(item_id)
    second = store.drop_quarantine_item(item_id)
    assert second is False


def test_quarantine_item_stores_payload(store):
    item_id = _add_item(store)
    item = store.get_quarantine_item(item_id)
    assert item["payload_json"] == '{"test":"payload"}'
    assert item["source_identity"] == "did:key:sender"


def test_quarantine_list_owner_only():
    from proxion_messenger_core.security_policy import SecurityPolicy
    policy = SecurityPolicy()
    for cmd in ("list_quarantine_items", "release_quarantine_item", "drop_quarantine_item"):
        decision = policy.evaluate_ws_command(
            cmd=cmd,
            caller_webid="did:key:non_owner",
            gateway_owner_did="did:key:owner",
        )
        assert not decision.allow, f"{cmd} should be owner-only"
