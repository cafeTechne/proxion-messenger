"""Tests for the monotonic delivery state machine."""
import pytest

from proxion_messenger_core.delivery_state import is_valid_transition, STATES
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_valid_state_progression_queued_to_read(store):
    store.save_message("msg-1", "thread-a", "dm", "did:web:alice.example", "Alice", "hi", "2024-01-01T00:00:00Z")

    for prev, nxt in zip(STATES, STATES[1:]):
        assert is_valid_transition(prev, nxt), f"{prev} -> {nxt} should be valid"

    assert store.set_message_delivery_state("msg-1", "did:web:bob.example", "queued")
    assert store.set_message_delivery_state("msg-1", "did:web:bob.example", "sent")
    assert store.set_message_delivery_state("msg-1", "did:web:bob.example", "delivered")
    assert store.set_message_delivery_state("msg-1", "did:web:bob.example", "read")

    state = store.get_message_delivery_state("msg-1", "did:web:bob.example")
    assert state is not None and state["state"] == "read"


def test_invalid_state_regression_rejected(store):
    store.save_message("msg-2", "thread-b", "dm", "did:web:alice.example", "Alice", "hi", "2024-01-01T00:00:00Z")
    store.set_message_delivery_state("msg-2", "did:web:bob.example", "read")

    rejected = store.set_message_delivery_state("msg-2", "did:web:bob.example", "sent")
    assert rejected is False

    state = store.get_message_delivery_state("msg-2", "did:web:bob.example")
    assert state["state"] == "read"


def test_multi_device_state_merge_monotonic(store):
    store.save_message("msg-3", "thread-c", "dm", "did:web:alice.example", "Alice", "hello", "2024-01-01T00:00:00Z")

    store.set_message_delivery_state("msg-3", "did:web:bob-device-1.example", "delivered")
    store.set_message_delivery_state("msg-3", "did:web:bob-device-2.example", "sent")

    state1 = store.get_message_delivery_state("msg-3", "did:web:bob-device-1.example")
    state2 = store.get_message_delivery_state("msg-3", "did:web:bob-device-2.example")
    assert state1["state"] == "delivered"
    assert state2["state"] == "sent"

    store.set_message_delivery_state("msg-3", "did:web:bob-device-1.example", "read")
    state1_updated = store.get_message_delivery_state("msg-3", "did:web:bob-device-1.example")
    assert state1_updated["state"] == "read"

    regress_ok = store.set_message_delivery_state("msg-3", "did:web:bob-device-1.example", "queued")
    assert regress_ok is False
