"""User presence tracking on federated Solid Pods."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .solid_client import SolidClient


PRESENCE_PATH = "stash://profile/presence.json"


@dataclass
class PresenceDoc:
    """User presence document.

    Parameters
    ----------
    status : str
        Presence status: "online", "away", "busy", or "offline".
    display_name : str
        User's display name.
    updated_at : str
        ISO 8601 timestamp of last update.
    status_text : str, optional
        Custom status message, e.g. "Playing Elden Ring".
    avatar_url : str, optional
        stash:// URI pointing to the user's avatar image.
    """
    status: str
    display_name: str
    updated_at: str
    status_text: Optional[str] = None
    avatar_url: Optional[str] = None


def set_presence(
    pod_client: SolidClient,
    status: str,
    display_name: str,
    status_text: Optional[str] = None,
    avatar_url: Optional[str] = None,
) -> None:
    """Set user presence on their Pod.

    Serializes PresenceDoc to JSON and PUTs to stash://profile/presence.json.

    Parameters
    ----------
    pod_client : SolidClient
        Authenticated client for the user's Pod.
    status : str
        Presence status: "online", "away", "busy", or "offline".
    display_name : str
        User's display name.
    status_text : str, optional
        Custom status message shown below the display name.
    avatar_url : str, optional
        stash:// URI for the user's avatar image.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    doc = PresenceDoc(
        status=status,
        display_name=display_name,
        updated_at=now_iso,
        status_text=status_text,
        avatar_url=avatar_url,
    )

    data = {
        "status": doc.status,
        "display_name": doc.display_name,
        "updated_at": doc.updated_at,
        "status_text": doc.status_text,
        "avatar_url": doc.avatar_url,
    }

    pod_client.put(PRESENCE_PATH, json.dumps(data).encode("utf-8"))


def get_presence(
    pod_client: SolidClient,
    stash_uri: str = PRESENCE_PATH,
) -> PresenceDoc:
    """Retrieve user presence from a Pod.
    
    GETs stash_uri and parses JSON. On 404 or parse failure, returns
    a default "offline" PresenceDoc.
    
    Parameters
    ----------
    pod_client : SolidClient
        Authenticated client.
    stash_uri : str
        stash:// URI to fetch (default: stash://profile/presence.json).
    
    Returns
    -------
    PresenceDoc
        The presence document (or "offline" default on error).
    """
    from .solid_client import SolidError
    
    try:
        raw = pod_client.get(stash_uri)
        data = json.loads(raw.decode("utf-8"))
        
        return PresenceDoc(
            status=data.get("status", "offline"),
            display_name=data.get("display_name", "Unknown"),
            updated_at=data.get("updated_at", datetime.now(timezone.utc).isoformat()),
            status_text=data.get("status_text"),
            avatar_url=data.get("avatar_url"),
        )
    except (SolidError, json.JSONDecodeError, KeyError, UnicodeDecodeError):
        # Return graceful default for 404 or parse errors
        return PresenceDoc(
            status="offline",
            display_name="Unknown",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
