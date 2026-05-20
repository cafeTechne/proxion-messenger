"""Tests for group sender key epoch convergence (Round 20)."""
import pytest
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.sender_keys import generate_sender_key, encrypt_group_message, decrypt_group_message


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_epoch_starts_at_one(store):
    """Default epoch for a newly saved sender key is 1."""
    store.save_sender_key("room-x", "alice@example.org", "ck==", 0)
    assert store.get_sender_key_epoch("room-x", "alice@example.org") == 1


def test_sender_key_rotation_bumps_epoch(store):
    """bump_sender_key_epoch increments the epoch and returns the new value."""
    store.save_sender_key("room-y", "bob@example.org", "ck==", 0)
    assert store.get_sender_key_epoch("room-y", "bob@example.org") == 1

    new_epoch = store.bump_sender_key_epoch("room-y", "bob@example.org")
    assert new_epoch == 2
    assert store.get_sender_key_epoch("room-y", "bob@example.org") == 2


def test_stale_epoch_payload_rejected():
    """decrypt_group_message raises ValueError when payload epoch < state epoch."""
    sender_state = generate_sender_key(epoch=2)
    _, payload = encrypt_group_message(sender_state, "hello", "alice@example.org")
    # Manually lower the epoch in the payload to simulate a stale message
    payload["sender_epoch"] = 1

    receiver_state = generate_sender_key(epoch=2)
    with pytest.raises(ValueError, match="sender_key_epoch_stale"):
        decrypt_group_message(receiver_state, payload)
