"""R10: Identity key continuity and rollover workflow tests."""
import time
import uuid
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_record_identity_key_first_seen(store):
    store.record_identity_key_seen("https://alice.example/profile", "aabbcc" * 10 + "aa", trusted=True)
    history = store.get_identity_key_history("https://alice.example/profile")
    assert len(history) == 1
    assert history[0]["trusted"] == 1


def test_known_webid_key_change_creates_rollover_event(store):
    identity = "https://alice.example/profile"
    old_key = "aa" * 32
    new_key = "bb" * 32
    store.record_identity_key_seen(identity, old_key, trusted=True)
    # Simulate seeing new key
    store.record_identity_key_seen(identity, new_key, trusted=False)
    event_id = str(uuid.uuid4())
    store.open_identity_rollover_event(
        id=event_id,
        identity=identity,
        old_pubkey_hex=old_key,
        new_pubkey_hex=new_key,
    )
    pending = store.get_pending_rollover_for_identity(identity)
    assert pending is not None
    assert pending["old_pubkey_hex"] == old_key
    assert pending["new_pubkey_hex"] == new_key


def test_unapproved_rollover_event_has_pending_status(store):
    identity = "https://bob.example/profile"
    eid = str(uuid.uuid4())
    store.open_identity_rollover_event(
        id=eid,
        identity=identity,
        old_pubkey_hex="cc" * 32,
        new_pubkey_hex="dd" * 32,
    )
    event = store.get_identity_rollover_event(eid)
    assert event["status"] == "pending"


def test_approved_rollover_updates_trust(store):
    identity = "https://carol.example/profile"
    old_key = "ee" * 32
    new_key = "ff" * 32
    store.record_identity_key_seen(identity, old_key, trusted=True)
    store.record_identity_key_seen(identity, new_key, trusted=False)
    eid = str(uuid.uuid4())
    store.open_identity_rollover_event(id=eid, identity=identity,
                                       old_pubkey_hex=old_key, new_pubkey_hex=new_key)
    # Approve
    resolved = store.resolve_identity_rollover_event(eid, "approved")
    assert resolved is True
    store.trust_identity_key(identity, new_key)
    assert store.is_trusted_identity_key(identity, new_key) is True


def test_is_trusted_returns_none_for_unseen_key(store):
    result = store.is_trusted_identity_key("https://unknown.example", "aa" * 32)
    assert result is None


def test_trust_identity_key_updates_record(store):
    identity = "https://dave.example"
    key = "12" * 32
    store.record_identity_key_seen(identity, key, trusted=False)
    assert store.is_trusted_identity_key(identity, key) is False
    store.trust_identity_key(identity, key)
    assert store.is_trusted_identity_key(identity, key) is True


def test_list_rollover_events_by_status(store):
    identity = "https://eve.example"
    id1 = str(uuid.uuid4())
    id2 = str(uuid.uuid4())
    store.open_identity_rollover_event(id=id1, identity=identity, old_pubkey_hex="11" * 32, new_pubkey_hex="22" * 32)
    store.open_identity_rollover_event(id=id2, identity=identity, old_pubkey_hex="22" * 32, new_pubkey_hex="33" * 32)
    store.resolve_identity_rollover_event(id1, "approved")
    pending = store.list_identity_rollover_events(status="pending")
    approved = store.list_identity_rollover_events(status="approved")
    assert any(e["id"] == id2 for e in pending)
    assert any(e["id"] == id1 for e in approved)
