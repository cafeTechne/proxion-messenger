import asyncio
import json
import logging
from typing import Callable, Awaitable, Optional

from .solid_client import SolidClient, SolidError

logger = logging.getLogger(__name__)

async def subscribe_to_resource(
    pod_client: SolidClient,
    stash_uri: str,
    callback: Callable[[str], Awaitable[None]],
    css_base_url: str,
) -> None:
    """Subscribe to real-time notification for a Solid resource using WebSocketChannel2023.
    
    This follows the Solid Notifications Protocol implemented by CSS 7+.
    """
    try:
        import websockets
    except ImportError:
        logger.error("The 'websockets' package is required for Solid Notifications.")
        return

    # 1. Discover the WebSocket endpoint
    discovery_url = f"{css_base_url.rstrip('/')}/.notifications/WebSocketChannel2023/"
    try:
        # Solid Notifications discovery usually requires the same auth as the resource
        response = pod_client.get(discovery_url)
        # response should be a JSON-LD doc with 'receiveFrom'
        data = json.loads(response)
        ws_endpoint = data.get("receiveFrom")
        if not ws_endpoint:
            logger.warning(f"Could not find 'receiveFrom' in discovery response at {discovery_url}")
            return
    except Exception as exc:
        logger.warning(f"Failed to discover notification endpoint for {css_base_url}: {exc}")
        return

    # 2. Convert stash:// to http://
    resource_url = pod_client._resolver.resolve(stash_uri)

    # 3. Connect and Subscribe
    try:
        async with websockets.connect(ws_endpoint) as websocket:
            # Send subscription request
            sub_request = {
                "@context": ["https://www.w3.org/ns/solid/notification/v1"],
                "type": "Subscribe",
                "topic": resource_url,
                "accept": "application/ld+json"
            }
            await websocket.send(json.dumps(sub_request))
            
            # 4. Listen for notifications
            async for message in websocket:
                try:
                    await callback(message)
                except Exception as exc:
                    logger.error(f"Error in notification callback: {exc}")
    except asyncio.CancelledError:
        logger.info(f"Subscription to {stash_uri} cancelled.")
        raise
    except Exception as exc:
        logger.warning(f"WebSocket error for {stash_uri}: {exc}")

async def watch_stash_uri(
    pod_client: SolidClient,
    stash_uri: str,
    on_change: Callable[[], Awaitable[None]],
    css_base_url: str,
) -> None:
    """Watch a stash URI and trigger on_change callback on any notification."""
    async def _on_notify(msg_str):
        # We don't strictly need to parse the JSON if we just want to know *something* changed
        await on_change()

    await subscribe_to_resource(pod_client, stash_uri, _on_notify, css_base_url)


# ---------------------------------------------------------------------------
# Local notification queue
# ---------------------------------------------------------------------------

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING
import uuid

if TYPE_CHECKING:
    from .stash import StashClient


@dataclass
class NotificationRecord:
    """A local notification record."""
    
    id: str
    event_type: str  # message, reaction, mention, invite, read_receipt
    title: str
    body: str
    data: dict
    created_iso: str
    read: bool = False
    
    def __post_init__(self):
        """Initialize timestamps if not provided."""
        if not self.created_iso:
            self.created_iso = datetime.now(timezone.utc).isoformat()


async def notify(
    stash,
    event_type: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> NotificationRecord:
    """Create and store a notification.
    
    Parameters
    ----------
    stash : StashClient
        Stash client for persistence.
    event_type : str
        Type of event (message, reaction, mention, invite, read_receipt).
    title : str
        Notification title.
    body : str
        Notification body/description.
    data : Optional[dict]
        Optional metadata dict (e.g., {"room_id": "...", "from_webid": "..."}).
    
    Returns
    -------
    NotificationRecord
        The created notification.
    """
    notification_id = str(uuid.uuid4())
    
    rec = NotificationRecord(
        id=notification_id,
        event_type=event_type,
        title=title,
        body=body,
        data=data or {},
        created_iso=datetime.now(timezone.utc).isoformat(),
        read=False,
    )
    
    key = f"notifications/{notification_id}.json"
    await stash.put(key, json.dumps(asdict(rec)).encode())
    
    return rec


async def get_notifications(
    stash,
    unread_only: bool = False,
    limit: int = 50,
) -> list[NotificationRecord]:
    """Retrieve notifications, optionally filtered to unread.
    
    Parameters
    ----------
    stash : StashClient
        Stash client for reading.
    unread_only : bool
        If True, only return unread notifications.
    limit : int
        Maximum number to return.
    
    Returns
    -------
    list[NotificationRecord]
        Notifications sorted newest-first.
    """
    notif_keys = await stash.list("notifications/")
    
    records = []
    for key in notif_keys:
        try:
            data = await stash.get(key)
            if data:
                rec_dict = json.loads(data.decode())
                rec = NotificationRecord(**rec_dict)
                if not unread_only or not rec.read:
                    records.append(rec)
        except Exception:
            continue
    
    # Sort newest-first
    records.sort(key=lambda r: r.created_iso, reverse=True)
    return records[:limit]


async def mark_notification_read(stash, notification_id: str) -> bool:
    """Mark a notification as read.
    
    Parameters
    ----------
    stash : StashClient
        Stash client for persistence.
    notification_id : str
        ID of the notification to mark read.
    
    Returns
    -------
    bool
        True if marked successfully, False if not found.
    """
    key = f"notifications/{notification_id}.json"
    try:
        data = await stash.get(key)
        if not data:
            return False
        
        rec_dict = json.loads(data.decode())
        rec = NotificationRecord(**rec_dict)
        rec.read = True
        
        await stash.put(key, json.dumps(asdict(rec)).encode())
        return True
    except Exception:
        return False


async def clear_notifications(
    stash,
    read_only: bool = True,
) -> int:
    """Clear notifications from storage.
    
    Parameters
    ----------
    stash : StashClient
        Stash client for deletion.
    read_only : bool
        If True, only delete read notifications. If False, delete all.
    
    Returns
    -------
    int
        Number of notifications deleted.
    """
    notif_keys = await stash.list("notifications/")
    deleted_count = 0
    
    for key in notif_keys:
        try:
            if read_only:
                data = await stash.get(key)
                if data:
                    rec_dict = json.loads(data.decode())
                    if not rec_dict.get("read", False):
                        continue
            
            await stash.delete(key)
            deleted_count += 1
        except Exception:
            continue
    
    return deleted_count

