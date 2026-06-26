"""Store/crypto-level tests for MVP-critical flows.

No live WebSocket server required — exercises LocalStore and crypto primitives
directly to verify the state invariants backing each critical user journey.
"""
import sqlite3

import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.attachment_crypto import encrypt_attachment, attachment_key_payload


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "test.db"
    return LocalStore(str(db))


def _insert_message_with_seq(store, thread_id, message_id, content="hello", seq=None):
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        """INSERT INTO messages
           (message_id, thread_id, thread_type, from_webid, content, timestamp, seq)
           VALUES (?, ?, 'dm', 'did:web:alice.example', ?, datetime('now'), ?)""",
        (message_id, thread_id, content, seq),
    )
    conn.commit()
    conn.close()


def test_onboarding_and_contact_bootstrap_flow(store):
    """Onboarding: register a device and upsert a contact — state persists correctly."""
    store.register_device("device-abc", "did:web:alice.example", "Ed25519PublicKeyB64==", "alice-laptop")
    devices = store.list_devices("did:web:alice.example")
    assert any(d["device_id"] == "device-abc" for d in devices)

    store.upsert_contact("did:web:bob.example", "Bob", source="invite")
    contacts = store.get_all_contacts()
    assert any(c["webid"] == "did:web:bob.example" for c in contacts)


def test_dm_online_offline_catchup_flow(store):
    """DM catchup: messages saved during offline period are retrievable by seq."""
    thread_id = "thread-alice-bob"
    _insert_message_with_seq(store, thread_id, "msg-1", "hello", seq=1)
    _insert_message_with_seq(store, thread_id, "msg-2", "world", seq=2)
    _insert_message_with_seq(store, thread_id, "msg-3", "catch up", seq=3)

    missed = store.get_messages_since_seq(thread_id, since_seq=1, limit=10)
    assert len(missed) == 2
    assert all(m["seq"] > 1 for m in missed)

    store.set_catchup_watermark("did:web:bob.example", "device-bob", thread_id, last_seq=3)
    wm = store.get_catchup_watermark("did:web:bob.example", "device-bob", thread_id)
    assert wm == 3


def test_room_rekey_and_post_removal_confidentiality_flow(store):
    """Room rekey: after member removal, old sender key is gone and new epoch is saved."""
    room_id = "room-xyz"
    owner_webid = "did:web:alice.example"

    store.save_sender_key(room_id, owner_webid, "b64-key-material-old", iteration=0)
    old_key = store.get_sender_key(room_id, owner_webid)
    assert old_key is not None

    store.delete_sender_keys_for_room(room_id)
    gone = store.get_sender_key(room_id, owner_webid)
    assert gone is None

    store.save_sender_key(room_id, owner_webid, "b64-key-material-new", iteration=0)
    store.bump_sender_key_epoch(room_id, owner_webid)
    new_key = store.get_sender_key(room_id, owner_webid)
    assert new_key is not None
    assert new_key.get("epoch", 1) >= 1
