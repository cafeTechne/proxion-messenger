"""Unified message inbox aggregating DMs and room messages."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .federation import RelationshipCertificate
    from .messaging import Message
    from .persist import AgentState
    from .solid_client import SolidClient
    from .room import RoomMembership


@dataclass
class InboxEntry:
    """A message in the unified inbox.
    
    Parameters
    ----------
    source : str
        Message source: "dm" for direct messages or "room" for room messages.
    cert : RelationshipCertificate
        The certificate used to access this message.
    message : Message
        The message object.
    """
    source: str
    cert: RelationshipCertificate
    message: Message


def poll_inbox(
    agent: AgentState,
    dm_clients: list[tuple[RelationshipCertificate, SolidClient]],
    room_memberships: list[tuple[RoomMembership, SolidClient]],
    since: Optional[datetime] = None,
) -> list[InboxEntry]:
    """Poll for new messages from DMs and rooms.
    
    For each (cert, client) pair: calls messaging.receive().
    For each (membership, client) pair: calls room.read_room().
    
    Merges results and sorts by message timestamp.
    
    Parameters
    ----------
    agent : AgentState
        The user's agent state.
    dm_clients : list[tuple[RelationshipCertificate, SolidClient]]
        List of (cert, client) pairs for DM threads.
    room_memberships : list[tuple[RoomMembership, SolidClient]]
        List of (membership, client) pairs for rooms.
    since : datetime, optional
        Only return messages newer than this timestamp.
    
    Returns
    -------
    list[InboxEntry]
        Aggregated and sorted messages.
    """
    from .messaging import receive
    from .room import read_room
    
    entries = []
    
    # Poll DMs
    for cert, client in dm_clients:
        messages = receive(
            cert=cert,
            pod_client=client,
            holder_state=agent,
            signing_key=agent.signing_key_bytes,
        )
        for msg in messages:
            if since:
                msg_time = datetime.fromtimestamp(msg.timestamp, tz=timezone.utc)
                if msg_time <= since:
                    continue
            entries.append(InboxEntry(source="dm", cert=cert, message=msg))

    # Poll rooms
    for membership, client in room_memberships:
        messages = read_room(
            membership=membership,
            pod_client=client,
            owner_agent=agent,
            since=since,
        )
        for msg in messages:
            entries.append(InboxEntry(source="room", cert=membership.cert, message=msg))

    # Sort by timestamp
    entries.sort(key=lambda e: e.message.timestamp)
    
    return entries


def watch_inbox(
    agent: AgentState,
    dm_clients: list[tuple[RelationshipCertificate, SolidClient]],
    room_memberships: list[tuple[RoomMembership, SolidClient]],
    callback: Callable[[InboxEntry], None],
    interval: float = 5.0,
) -> threading.Thread:
    """Start a background daemon thread polling for new messages.
    
    Calls poll_inbox() every `interval` seconds and invokes callback()
    for each new entry. Deduplicates by message ID (doesn't call callback
    for the same message twice).
    
    Parameters
    ----------
    agent : AgentState
        The user's agent state.
    dm_clients : list[tuple[RelationshipCertificate, SolidClient]]
        DM thread clients.
    room_memberships : list[tuple[RoomMembership, SolidClient]]
        Room memberships.
    callback : Callable[[InboxEntry], None]
        Function to call for each new entry.
    interval : float
        Polling interval in seconds (default: 5.0).
    
    Returns
    -------
    threading.Thread
        The daemon thread. Caller can .join() or let it run as daemon.
    """
    seen_message_ids = set()
    last_poll_time = None
    
    def poll_and_callback():
        nonlocal last_poll_time
        while True:
            try:
                entries = poll_inbox(
                    agent=agent,
                    dm_clients=dm_clients,
                    room_memberships=room_memberships,
                    since=last_poll_time,
                )
                
                for entry in entries:
                    msg_id = entry.message.message_id if hasattr(entry.message, "message_id") else str(entry.message)
                    if msg_id not in seen_message_ids:
                        seen_message_ids.add(msg_id)
                        callback(entry)
                
                if entries:
                    last_poll_time = datetime.fromtimestamp(entries[-1].message.timestamp, tz=timezone.utc)
            except Exception:
                pass  # Silently ignore poll errors in background thread
            
            time.sleep(interval)
    
    thread = threading.Thread(target=poll_and_callback, daemon=True)
    thread.start()
    
    return thread
