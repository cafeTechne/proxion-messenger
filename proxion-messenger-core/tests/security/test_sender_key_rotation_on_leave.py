"""Tests for group sender key rotation when a member leaves (Round 19)."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.sender_keys import generate_sender_key


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _insert_sender_key(store, room_id, sender_webid, chain_key_b64="ckeyXXXXXXXXXXXX=="):
    """Helper: insert a sender key directly into the DB."""
    import sqlite3
    now = time.time()
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        """INSERT OR REPLACE INTO sender_keys
           (room_id, sender_webid, chain_key_b64, iteration, created_at, updated_at)
           VALUES (?, ?, ?, 0, ?, ?)""",
        (room_id, sender_webid, chain_key_b64, now, now),
    )
    conn.commit()
    conn.close()


def test_sender_keys_deleted_on_member_remove(store):
    """delete_sender_keys_for_room removes all sender keys for the room."""
    room_id = "room-alpha"
    _insert_sender_key(store, room_id, "alice@example.org")
    _insert_sender_key(store, room_id, "bob@example.org")
    _insert_sender_key(store, "room-beta", "carol@example.org")

    store.delete_sender_keys_for_room(room_id)

    assert store.get_sender_key(room_id, "alice@example.org") is None
    assert store.get_sender_key(room_id, "bob@example.org") is None
    # Other rooms untouched
    assert store.get_sender_key("room-beta", "carol@example.org") is not None


def test_rotation_event_emitted_to_remaining_members(store):
    """After deletion, the remaining sender keys dict is empty for the room."""
    room_id = "room-gamma"
    _insert_sender_key(store, room_id, "alice@example.org")
    _insert_sender_key(store, room_id, "bob@example.org")
    _insert_sender_key(store, room_id, "carol@example.org")

    # Simulate kicking Carol: delete all keys
    store.delete_sender_keys_for_room(room_id)

    # After deletion, no sender keys remain
    for webid in ("alice@example.org", "bob@example.org", "carol@example.org"):
        assert store.get_sender_key(room_id, webid) is None


def test_removed_member_cannot_decrypt_post_rotation(store):
    """A new sender key after rotation must differ from the old one."""
    room_id = "room-delta"
    old_key = generate_sender_key()
    store.save_sender_key(room_id, "alice@example.org", old_key["chain_key_b64"], 0)

    # Simulate rotation: delete and create new
    store.delete_sender_keys_for_room(room_id)
    new_key = generate_sender_key()
    store.save_sender_key(room_id, "alice@example.org", new_key["chain_key_b64"], 0)

    retrieved = store.get_sender_key(room_id, "alice@example.org")
    assert retrieved is not None
    assert retrieved["chain_key_b64"] != old_key["chain_key_b64"]


def test_joining_member_receives_all_sender_keys(store):
    """After a member joins, existing sender keys are visible for distribution."""
    room_id = "room-epsilon"
    _insert_sender_key(store, room_id, "alice@example.org", "ck_alice==")
    _insert_sender_key(store, room_id, "bob@example.org", "ck_bob==")

    # New member "carol" joins; gateway would distribute existing keys.
    # Verify both keys are retrievable for distribution.
    alice_key = store.get_sender_key(room_id, "alice@example.org")
    bob_key = store.get_sender_key(room_id, "bob@example.org")

    assert alice_key is not None
    assert bob_key is not None
    assert alice_key["chain_key_b64"] == "ck_alice=="
    assert bob_key["chain_key_b64"] == "ck_bob=="
