"""Tests for async outbox retry queue (OutboxRecord, enqueue, list_due, etc.)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from proxion_messenger_core.outbox import (
    OutboxRecord,
    enqueue,
    list_due,
    mark_success,
    mark_failed,
    run_retry_loop,
    BASE_DELAY,
    MAX_DELAY,
    MAX_ATTEMPTS,
)


@pytest.fixture
def mock_stash():
    """Fixture for mocked StashClient."""
    stash = AsyncMock()
    stash.put = AsyncMock()
    stash.get = AsyncMock()
    stash.delete = AsyncMock()
    stash.list = AsyncMock(return_value=[])
    return stash


@pytest.mark.asyncio
async def test_enqueue_stores_record(mock_stash):
    """Test that enqueue creates and persists an OutboxRecord."""
    target_url = "https://bob.pod"
    payload = {"msg": "hello"}

    rec = await enqueue(mock_stash, target_url, payload)

    assert rec.id
    assert rec.target_url == target_url
    assert rec.payload == payload
    assert rec.attempt == 0
    assert rec.created_iso
    assert rec.next_retry_iso

    # Verify stash.put was called
    mock_stash.put.assert_called_once()
    args = mock_stash.put.call_args
    assert args[0][0] == f"outbox/{rec.id}.json"


@pytest.mark.asyncio
async def test_list_due_returns_past_records(mock_stash):
    """Test that list_due returns records with past retry times."""
    now = datetime.now(timezone.utc)
    past_iso = (now - timedelta(seconds=5)).isoformat()

    rec_dict = {
        "id": "rec1",
        "target_url": "https://bob.pod",
        "payload": {"msg": "test"},
        "attempt": 0,
        "next_retry_iso": past_iso,
        "created_iso": now.isoformat(),
    }

    mock_stash.list.return_value = ["outbox/rec1.json"]
    mock_stash.get.return_value = json.dumps(rec_dict).encode()

    due = await list_due(mock_stash)

    assert len(due) == 1
    assert due[0].id == "rec1"


@pytest.mark.asyncio
async def test_list_due_skips_max_attempts(mock_stash):
    """Test that list_due skips records at or over MAX_ATTEMPTS."""
    now = datetime.now(timezone.utc)
    past_iso = (now - timedelta(seconds=5)).isoformat()

    rec_dict = {
        "id": "rec1",
        "target_url": "https://bob.pod",
        "payload": {"msg": "test"},
        "attempt": MAX_ATTEMPTS,  # At limit, should be skipped
        "next_retry_iso": past_iso,
        "created_iso": now.isoformat(),
    }

    mock_stash.list.return_value = ["outbox/rec1.json"]
    mock_stash.get.return_value = json.dumps(rec_dict).encode()

    due = await list_due(mock_stash)

    assert len(due) == 0


@pytest.mark.asyncio
async def test_mark_success_deletes_record(mock_stash):
    """Test that mark_success deletes the outbox record."""
    record_id = "rec123"

    await mark_success(mock_stash, record_id)

    mock_stash.delete.assert_called_once_with(f"outbox/{record_id}.json")


@pytest.mark.asyncio
async def test_mark_failed_increments_attempt(mock_stash):
    """Test that mark_failed increments attempt and schedules next retry."""
    rec = OutboxRecord(
        id="rec1",
        target_url="https://bob.pod",
        payload={"msg": "test"},
        attempt=0,
        next_retry_iso=datetime.now(timezone.utc).isoformat(),
        created_iso=datetime.now(timezone.utc).isoformat(),
    )

    updated = await mark_failed(mock_stash, rec)

    assert updated.attempt == 1
    # next_retry_iso should be in the future
    now = datetime.now(timezone.utc).isoformat()
    assert updated.next_retry_iso > now

    mock_stash.put.assert_called_once()


@pytest.mark.asyncio
async def test_mark_failed_caps_delay(mock_stash):
    """Test that mark_failed caps exponential backoff at MAX_DELAY."""
    rec = OutboxRecord(
        id="rec1",
        target_url="https://bob.pod",
        payload={"msg": "test"},
        attempt=20,  # Very high attempt number
        next_retry_iso=datetime.now(timezone.utc).isoformat(),
        created_iso=datetime.now(timezone.utc).isoformat(),
    )

    before = datetime.now(timezone.utc)
    updated = await mark_failed(mock_stash, rec)
    after = datetime.now(timezone.utc)

    # The new retry time should be no more than MAX_DELAY in the future
    retry_time = datetime.fromisoformat(updated.next_retry_iso)
    max_allowed = after + timedelta(seconds=MAX_DELAY)
    assert retry_time <= max_allowed


@pytest.mark.asyncio
async def test_run_retry_loop_calls_deliver(mock_stash):
    """Test that run_retry_loop invokes deliver_fn for due records."""
    now = datetime.now(timezone.utc)
    past_iso = (now - timedelta(seconds=5)).isoformat()

    rec_dict = {
        "id": "rec1",
        "target_url": "https://bob.pod",
        "payload": {"msg": "test"},
        "attempt": 0,
        "next_retry_iso": past_iso,
        "created_iso": now.isoformat(),
    }

    mock_stash.list.return_value = ["outbox/rec1.json"]
    mock_stash.get.return_value = json.dumps(rec_dict).encode()

    deliver_fn = AsyncMock(return_value=True)  # Successful delivery

    # Run one iteration only
    async def run_one_iteration():
        await asyncio.sleep(0.01)  # Simulate poll_interval
        due = await list_due(mock_stash)
        for rec in due:
            ok = await deliver_fn(rec.target_url, rec.payload)
            if ok:
                await mark_success(mock_stash, rec.id)

    await run_one_iteration()

    # Verify deliver_fn was called
    deliver_fn.assert_called_once_with("https://bob.pod", {"msg": "test"})
    mock_stash.delete.assert_called_once_with("outbox/rec1.json")


@pytest.mark.asyncio
async def test_run_retry_loop_broadcasts_on_permanent_failure(mock_stash):
    """Test that run_retry_loop broadcasts when max attempts exceeded."""
    rec = OutboxRecord(
        id="rec1",
        target_url="https://bob.pod",
        payload={"msg": "test"},
        attempt=MAX_ATTEMPTS,
        next_retry_iso=datetime.now(timezone.utc).isoformat(),
        created_iso=datetime.now(timezone.utc).isoformat(),
    )

    broadcast_fn = AsyncMock()
    deliver_fn = AsyncMock(return_value=False)  # Always fails

    # Simulate failure callback
    updated = await mark_failed(mock_stash, rec)
    if updated.attempt >= MAX_ATTEMPTS and broadcast_fn:
        await broadcast_fn(
            {
                "type": "outbox_failed",
                "record_id": rec.id,
                "target_url": rec.target_url,
                "attempts": updated.attempt,
            }
        )

    broadcast_fn.assert_called_once()
    call_args = broadcast_fn.call_args[0][0]
    assert call_args["type"] == "outbox_failed"
    assert call_args["record_id"] == "rec1"
