"""Read receipts module for tracking message reads.

Read receipts tell the sender that a specific message has been opened.
Stored on the reader's own Pod — the sender polls for them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .solid_client import SolidClient


@dataclass
class ReadReceipt:
    """A read receipt for a message.
    
    Parameters
    ----------
    message_id : str
        The message that was read.
    thread_id : str
        The thread identifier ("dm:<cert_id>" or "room:<room_id>").
    reader_webid : str
        WebID of the reader.
    read_at : str
        ISO 8601 timestamp when the message was read.
    """
    message_id: str
    thread_id: str
    reader_webid: str
    read_at: str


def mark_message_read(
    pod_client: SolidClient,
    message_id: str,
    thread_id: str,
    reader_webid: str,
) -> ReadReceipt:
    """PUT a read receipt to stash://receipts/{thread_id}/{message_id}.json.
    
    Parameters
    ----------
    pod_client : SolidClient
        Authenticated client for the reader's Pod.
    message_id : str
        The message ID that was read.
    thread_id : str
        The thread identifier.
    reader_webid : str
        WebID of the reader.
    
    Returns
    -------
    ReadReceipt
        The read receipt that was created.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    
    receipt = ReadReceipt(
        message_id=message_id,
        thread_id=thread_id,
        reader_webid=reader_webid,
        read_at=now_iso,
    )
    
    # Store on the reader's Pod
    receipt_path = f"stash://receipts/{thread_id}/{message_id}.json"
    receipt_data = {
        "message_id": receipt.message_id,
        "thread_id": receipt.thread_id,
        "reader_webid": receipt.reader_webid,
        "read_at": receipt.read_at,
    }
    
    pod_client.put(
        receipt_path,
        json.dumps(receipt_data).encode("utf-8"),
    )
    
    return receipt


def get_read_receipts(
    pod_client: SolidClient,
    thread_id: str,
    message_id: Optional[str] = None,
) -> list[ReadReceipt]:
    """List read receipts for a thread or a specific message.
    
    Lists stash://receipts/{thread_id}/.
    If message_id is given, returns only receipts for that message.
    
    Parameters
    ----------
    pod_client : SolidClient
        Authenticated client for the reader's Pod.
    thread_id : str
        The thread identifier.
    message_id : str, optional
        If provided, only return receipts for this message.
    
    Returns
    -------
    list[ReadReceipt]
        List of read receipts. Empty list if directory doesn't exist or is empty.
    """
    from .solid_client import SolidError
    
    receipts_dir = f"stash://receipts/{thread_id}/"
    receipts = []
    
    try:
        entries = pod_client.list(receipts_dir)
        for entry_url in entries:
            if entry_url.endswith(".json"):
                try:
                    raw = pod_client.get(entry_url)
                    data = json.loads(raw.decode("utf-8"))
                    
                    # Filter by message_id if specified
                    if message_id and data.get("message_id") != message_id:
                        continue
                    
                    receipt = ReadReceipt(
                        message_id=data.get("message_id", ""),
                        thread_id=data.get("thread_id", ""),
                        reader_webid=data.get("reader_webid", ""),
                        read_at=data.get("read_at", ""),
                    )
                    receipts.append(receipt)
                except (json.JSONDecodeError, KeyError, ValueError):
                    # Skip malformed entries
                    pass
    except (SolidError, Exception):
        # Return empty list if directory doesn't exist
        pass
    
    return receipts


def has_been_read(
    pod_client: SolidClient,
    thread_id: str,
    message_id: str,
) -> bool:
    """Return True if a read receipt exists for message_id in thread_id.
    
    Parameters
    ----------
    pod_client : SolidClient
        Authenticated client for the reader's Pod.
    thread_id : str
        The thread identifier.
    message_id : str
        The message ID to check.
    
    Returns
    -------
    bool
        True if a read receipt exists, False otherwise.
    """
    receipts = get_read_receipts(pod_client, thread_id, message_id)
    return len(receipts) > 0
