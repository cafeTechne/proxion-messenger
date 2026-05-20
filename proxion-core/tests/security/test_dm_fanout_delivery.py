"""Tests for per-device DM fanout delivery tracking (Round 20)."""
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_send_dm_fanout_persists_per_device_delivery_rows(store):
    """record_dm_delivery creates one row per (message_id, to_webid, to_device_id)."""
    message_id = "msg-fanout-001"
    store.record_dm_delivery(message_id, "bob@example.org", "bob-dev-1")
    store.record_dm_delivery(message_id, "bob@example.org", "bob-dev-2")

    rows = store.get_dm_deliveries(message_id)
    assert len(rows) == 2
    device_ids = {r["to_device_id"] for r in rows}
    assert device_ids == {"bob-dev-1", "bob-dev-2"}


def test_unknown_target_device_returns_empty_deliveries(store):
    """get_dm_deliveries returns empty list for a message with no delivery rows."""
    rows = store.get_dm_deliveries("nonexistent-message-id")
    assert rows == []


def test_fanout_does_not_duplicate_logical_message_id(store):
    """Inserting the same (message_id, to_webid, to_device_id) twice does not create duplicates."""
    message_id = "msg-dedup-001"
    store.record_dm_delivery(message_id, "carol@example.org", "carol-dev-1")
    store.record_dm_delivery(message_id, "carol@example.org", "carol-dev-1")  # duplicate

    rows = store.get_dm_deliveries(message_id)
    assert len(rows) == 1


