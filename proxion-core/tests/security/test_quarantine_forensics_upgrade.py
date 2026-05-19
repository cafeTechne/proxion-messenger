"""R12: Quarantine forensics upgrade tests."""
import hashlib
import time
import uuid
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _payload():
    return b'{"msg":"test","from_webid":"did:key:alice"}'


def _add(store, payload=None, item_type="relay", source_ip="1.2.3.4"):
    payload = payload or _payload()
    item_id = str(uuid.uuid4())
    sha256 = hashlib.sha256(payload).hexdigest()
    store.add_quarantine_item(
        id=item_id,
        item_type=item_type,
        source_identity="did:key:alice",
        payload_json=payload.decode("utf-8"),
        reason="test",
        created_at=time.time(),
        payload_sha256=sha256,
        source_ip=source_ip,
    )
    return item_id, sha256


def test_quarantine_records_payload_hash_and_source_ip(store):
    item_id, sha256 = _add(store, source_ip="10.0.0.1")
    item = store.get_quarantine_item(item_id)
    assert item["payload_sha256"] == sha256
    assert item["source_ip"] == "10.0.0.1"


def test_duplicate_quarantine_payload_rejected_within_window(store):
    payload = _payload()
    sha256 = hashlib.sha256(payload).hexdigest()
    _add(store, payload=payload)
    # Same payload again within window — should be flagged as duplicate
    assert store.has_duplicate_quarantine_payload("relay", sha256) is True


def test_different_payload_not_flagged_as_duplicate(store):
    _add(store, payload=b'{"msg":"first"}')
    other_sha256 = hashlib.sha256(b'{"msg":"second"}').hexdigest()
    assert store.has_duplicate_quarantine_payload("relay", other_sha256) is False


def test_different_item_type_not_flagged_as_duplicate(store):
    payload = _payload()
    sha256 = hashlib.sha256(payload).hexdigest()
    _add(store, payload=payload, item_type="relay")
    # Same payload but different type — not a duplicate for "invite"
    assert store.has_duplicate_quarantine_payload("invite", sha256) is False


def test_release_timestamp_recorded(store):
    item_id, _ = _add(store)
    before = time.time()
    store.release_quarantine_item(item_id)
    item = store.get_quarantine_item(item_id)
    assert item["released_at"] is not None
    assert item["released_at"] >= before


def test_drop_timestamp_recorded(store):
    item_id, _ = _add(store)
    before = time.time()
    store.drop_quarantine_item(item_id)
    item = store.get_quarantine_item(item_id)
    assert item["dropped_at"] is not None
    assert item["dropped_at"] >= before


def test_quarantine_item_without_sha256_still_works(store):
    """Backward compat: add_quarantine_item without payload_sha256 should not fail."""
    item_id = str(uuid.uuid4())
    store.add_quarantine_item(
        id=item_id,
        item_type="relay",
        source_identity="did:key:test",
        payload_json='{"test":1}',
        reason="legacy",
        created_at=time.time(),
    )
    item = store.get_quarantine_item(item_id)
    assert item is not None
