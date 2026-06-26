"""Tests for hole punch attempt expiry (stale attempt cleanup)."""
import time

import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.wg_overlay import generate_wg_keypair
from proxion_messenger_core.hole_punch import (
    HolePunchCoordinator,
    PUNCH_STATE_EXPIRED,
    PUNCH_STATE_SUCCEEDED,
    PUNCH_STATE_FAILED,
    HOLE_PUNCH_TIMEOUT_SECONDS,
)

INITIATOR = "did:web:alice.example"
RESPONDER = "did:web:peer.example"


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


@pytest.fixture
def coordinator(store):
    return HolePunchCoordinator(store)


def test_expire_stale_marks_old_pending_as_expired(coordinator, store):
    attempt_id = coordinator.initiate(INITIATOR, RESPONDER, "1.2.3.4", 5000)
    with store._conn() as conn:
        conn.execute(
            "UPDATE hole_punch_attempts SET initiated_at=? WHERE id=?",
            (time.time() - HOLE_PUNCH_TIMEOUT_SECONDS - 1, attempt_id),
        )
    expired = coordinator.expire_stale(timeout_seconds=HOLE_PUNCH_TIMEOUT_SECONDS)
    assert expired == 1
    attempt = coordinator.get_attempt(attempt_id)
    assert attempt["state"] == PUNCH_STATE_EXPIRED


def test_expire_stale_does_not_touch_recent_attempts(coordinator):
    attempt_id = coordinator.initiate(INITIATOR, RESPONDER, "1.2.3.4", 5000)
    expired = coordinator.expire_stale(timeout_seconds=HOLE_PUNCH_TIMEOUT_SECONDS)
    assert expired == 0
    attempt = coordinator.get_attempt(attempt_id)
    assert attempt["state"] != PUNCH_STATE_EXPIRED


def test_expire_stale_skips_terminal_succeeded(coordinator, store):
    _, pub_b64 = generate_wg_keypair()
    store.upsert_wg_peer(RESPONDER, pub_b64, None, "10.0.0.3/32", "relay")

    attempt_id = coordinator.initiate(INITIATOR, RESPONDER, "1.2.3.4", 5000)
    coordinator.record_offer(attempt_id)
    coordinator.record_peer_endpoint(attempt_id, INITIATOR, "9.9.9.9", 1234)
    coordinator.mark_succeeded(attempt_id, INITIATOR)

    with store._conn() as conn:
        conn.execute(
            "UPDATE hole_punch_attempts SET initiated_at=? WHERE id=?",
            (time.time() - HOLE_PUNCH_TIMEOUT_SECONDS - 10, attempt_id),
        )
    expired = coordinator.expire_stale()
    assert expired == 0
    attempt = coordinator.get_attempt(attempt_id)
    assert attempt["state"] == PUNCH_STATE_SUCCEEDED


def test_expire_stale_skips_terminal_failed(coordinator, store):
    attempt_id = coordinator.initiate(INITIATOR, RESPONDER, "1.2.3.4", 5000)
    coordinator.record_offer(attempt_id)
    coordinator.mark_failed(attempt_id, INITIATOR)

    with store._conn() as conn:
        conn.execute(
            "UPDATE hole_punch_attempts SET initiated_at=? WHERE id=?",
            (time.time() - HOLE_PUNCH_TIMEOUT_SECONDS - 10, attempt_id),
        )
    expired = coordinator.expire_stale()
    assert expired == 0
    attempt = coordinator.get_attempt(attempt_id)
    assert attempt["state"] == PUNCH_STATE_FAILED


def test_expire_stale_returns_count(coordinator, store):
    for i in range(3):
        attempt_id = coordinator.initiate(INITIATOR, f"did:web:peer{i}.example", "1.2.3.4", 5000 + i)
        with store._conn() as conn:
            conn.execute(
                "UPDATE hole_punch_attempts SET initiated_at=? WHERE id=?",
                (time.time() - HOLE_PUNCH_TIMEOUT_SECONDS - 1, attempt_id),
            )
    expired = coordinator.expire_stale()
    assert expired == 3
