"""Offline message queue (Outbox) for persisting unsent messages.

Also includes async retry queue for failed federation deliveries.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Callable, Awaitable, TYPE_CHECKING

from .messaging import Message, send

if TYPE_CHECKING:
    from .stash import StashClient

logger = logging.getLogger(__name__)

# Retry queue constants
BASE_DELAY = 10  # seconds
MAX_DELAY = 3600  # seconds (1 hour)
MAX_ATTEMPTS = 10


@dataclass
class OutboxItem:
    """A message waiting in the outbox."""
    item_id: str
    message: Message
    target_cert_id: Optional[str] = None
    room_id: Optional[str] = None


class Outbox:
    """Manages persistence and retrieval of unsent messages."""
    
    def __init__(self, storage_dir: str):
        self.storage_dir = storage_dir
        os.makedirs(self.storage_dir, exist_ok=True)

    def enqueue(self, message: Message, target_cert_id: Optional[str] = None, room_id: Optional[str] = None):
        """Save a message to the outbox for later delivery."""
        item_id = message.message_id
        path = os.path.join(self.storage_dir, f"{item_id}.json")
        data = {
            "target_cert_id": target_cert_id,
            "room_id": room_id,
            "message": message.to_dict()
        }
        with open(path, "w") as f:
            json.dump(data, f)
        logger.info(f"Enqueued message {item_id} to outbox")

    def get_items(self) -> List[OutboxItem]:
        """List all pending items in the outbox."""
        items = []
        if not os.path.exists(self.storage_dir):
            return items
            
        for filename in os.listdir(self.storage_dir):
            if filename.endswith(".json"):
                path = os.path.join(self.storage_dir, filename)
                try:
                    with open(path, "r") as f:
                        data = json.load(f)
                    items.append(OutboxItem(
                        item_id=filename[:-5],
                        message=Message.from_dict(data["message"]),
                        target_cert_id=data.get("target_cert_id"),
                        room_id=data.get("room_id")
                    ))
                except Exception as e:
                    logger.error(f"Failed to load outbox item {filename}: {e}")
        return items

    def remove(self, item_id: str):
        """Remove an item from the outbox."""
        path = os.path.join(self.storage_dir, f"{item_id}.json")
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError as e:
                logger.error(f"Failed to remove outbox item {item_id}: {e}")

    def clear(self):
        """Remove all items from the outbox."""
        for item in self.get_items():
            self.remove(item.item_id)


# ============================================================================
# Async retry queue for failed federation message delivery
# ============================================================================


@dataclass
class OutboxRecord:
    """A single outbound message awaiting delivery with retry metadata."""

    id: str
    target_url: str
    payload: dict
    attempt: int = 0
    next_retry_iso: str = ""
    created_iso: str = ""

    def __post_init__(self):
        """Initialize timestamps if not provided."""
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_iso:
            self.created_iso = now
        if not self.next_retry_iso:
            self.next_retry_iso = now


async def enqueue(stash: StashClient, target_url: str, payload: dict) -> OutboxRecord:
    """Enqueue a new outbound message for retry.

    Parameters
    ----------
    stash : StashClient
        Stash client for persistence.
    target_url : str
        Destination URL for delivery.
    payload : dict
        Message payload.

    Returns
    -------
    OutboxRecord
        The created outbox record.
    """
    rec = OutboxRecord(
        id=str(uuid.uuid4()),
        target_url=target_url,
        payload=payload,
    )
    key = f"outbox/{rec.id}.json"
    await stash.put(key, json.dumps(asdict(rec)).encode())
    return rec


async def list_due(stash: StashClient) -> list[OutboxRecord]:
    """List all outbox records ready for retry.

    Filters records where next_retry_iso <= now AND attempt < MAX_ATTEMPTS.

    Parameters
    ----------
    stash : StashClient
        Stash client for reading.

    Returns
    -------
    list[OutboxRecord]
        Records ready for retry.
    """
    records = []
    now = datetime.now(timezone.utc).isoformat()

    try:
        keys = await stash.list("outbox/")
    except Exception:
        return []

    for key in keys:
        try:
            data = await stash.get(key)
            record_dict = json.loads(data.decode())
            rec = OutboxRecord(**record_dict)

            # Check if ready for retry
            if rec.next_retry_iso <= now and rec.attempt < MAX_ATTEMPTS:
                records.append(rec)
        except Exception:
            # Skip malformed records
            pass

    return records


async def mark_success(stash: StashClient, record_id: str) -> None:
    """Mark an outbox record as successfully delivered.

    Removes the record from the queue.

    Parameters
    ----------
    stash : StashClient
        Stash client for deletion.
    record_id : str
        ID of the record to remove.
    """
    key = f"outbox/{record_id}.json"
    try:
        await stash.delete(key)
    except Exception:
        pass


async def mark_failed(stash: StashClient, rec: OutboxRecord) -> OutboxRecord:
    """Mark an outbox record as failed and schedule retry.

    Updates attempt count and next_retry_iso with exponential backoff.

    Parameters
    ----------
    stash : StashClient
        Stash client for persistence.
    rec : OutboxRecord
        Record to update.

    Returns
    -------
    OutboxRecord
        Updated record with new retry time.
    """
    rec.attempt += 1
    delay = min(BASE_DELAY * (2 ** (rec.attempt - 1)), MAX_DELAY)
    now = datetime.now(timezone.utc)
    rec.next_retry_iso = (now + timedelta(seconds=delay)).isoformat()

    key = f"outbox/{rec.id}.json"
    await stash.put(key, json.dumps(asdict(rec)).encode())

    return rec


async def run_retry_loop(
    stash: StashClient,
    deliver_fn: Callable[[str, dict], Awaitable[bool]],
    broadcast_fn: Optional[Callable[[dict], Awaitable[None]]] = None,
    poll_interval: float = 30.0,
) -> None:
    """Run the outbox retry loop indefinitely.

    Periodically checks for due records and attempts delivery. On success,
    removes the record. On failure, schedules a retry. After MAX_ATTEMPTS,
    broadcasts a failure event if broadcast_fn is provided.

    Parameters
    ----------
    stash : StashClient
        Stash client for persistence.
    deliver_fn : Callable[[str, dict], Awaitable[bool]]
        Async function(target_url, payload) -> bool. True on success, False to retry.
    broadcast_fn : Optional[Callable[[dict], Awaitable[None]]]
        Optional async callback(event_dict) for failed messages.
    poll_interval : float
        Seconds between retry checks (default 30).
    """
    while True:
        await asyncio.sleep(poll_interval)

        due = await list_due(stash)

        for rec in due:
            try:
                ok = await deliver_fn(rec.target_url, rec.payload)

                if ok:
                    await mark_success(stash, rec.id)
                else:
                    updated = await mark_failed(stash, rec)
                    if updated.attempt >= MAX_ATTEMPTS and broadcast_fn:
                        await broadcast_fn(
                            {
                                "type": "outbox_failed",
                                "record_id": rec.id,
                                "target_url": rec.target_url,
                                "attempts": updated.attempt,
                            }
                        )
            except Exception:
                # Catch delivery errors and mark failed
                updated = await mark_failed(stash, rec)
                if updated.attempt >= MAX_ATTEMPTS and broadcast_fn:
                    await broadcast_fn(
                        {
                            "type": "outbox_failed",
                            "record_id": rec.id,
                            "target_url": rec.target_url,
                            "attempts": updated.attempt,
                        }
                    )
