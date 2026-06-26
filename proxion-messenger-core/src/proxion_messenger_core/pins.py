"""Message pinning for DM threads and rooms."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from .solid_client import SolidClient
    from .messaging import Message


@dataclass
class PinnedMessage:
    """A pinned message reference stored on a Pod."""
    message_id: str
    thread_id: str        # "dm:<cert_id>" or "room:<room_id>"
    content_preview: str  # first 100 chars of message content
    pinned_by_webid: str
    pinned_at: str        # ISO 8601


def pin_message(
    pod_client: SolidClient,
    message: Message,
    thread_id: str,
    pinned_by_webid: str,
) -> PinnedMessage:
    """Pin a message in a thread.

    Writes a JSON document to ``stash://pins/{thread_id}/{message_id}.json``
    and returns the resulting :class:`PinnedMessage`.
    """
    preview = (message.content or "")[:100]
    pinned_at = datetime.now(timezone.utc).isoformat()

    pinned = PinnedMessage(
        message_id=message.message_id,
        thread_id=thread_id,
        content_preview=preview,
        pinned_by_webid=pinned_by_webid,
        pinned_at=pinned_at,
    )

    path = f"stash://pins/{thread_id}/{message.message_id}.json"
    data = {
        "message_id": pinned.message_id,
        "thread_id": pinned.thread_id,
        "content_preview": pinned.content_preview,
        "pinned_by_webid": pinned.pinned_by_webid,
        "pinned_at": pinned.pinned_at,
    }
    pod_client.put(path, json.dumps(data).encode("utf-8"))
    return pinned


def unpin_message(
    pod_client: SolidClient,
    message_id: str,
    thread_id: str,
) -> None:
    """Remove a pinned message from a thread.

    Deletes ``stash://pins/{thread_id}/{message_id}.json``.  If the document
    does not exist the call is silently ignored.
    """
    path = f"stash://pins/{thread_id}/{message_id}.json"
    try:
        pod_client.delete(path)
    except Exception:
        pass


def get_pinned_messages(
    pod_client: SolidClient,
    thread_id: str,
) -> List[PinnedMessage]:
    """Return all pinned messages for a thread.

    Lists ``stash://pins/{thread_id}/`` and reads each document.  Documents
    that cannot be parsed are skipped.
    """
    from .solid_client import SolidError

    container = f"stash://pins/{thread_id}/"
    try:
        entries = pod_client.list(container)
    except SolidError:
        return []

    pinned: List[PinnedMessage] = []
    for uri in entries:
        try:
            raw = pod_client.get(uri)
            data = json.loads(raw.decode("utf-8"))
            pinned.append(PinnedMessage(
                message_id=data["message_id"],
                thread_id=data["thread_id"],
                content_preview=data.get("content_preview", ""),
                pinned_by_webid=data["pinned_by_webid"],
                pinned_at=data["pinned_at"],
            ))
        except Exception:
            continue

    return pinned
