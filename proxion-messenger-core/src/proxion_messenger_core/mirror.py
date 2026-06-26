"""Room mirroring for local backup and offline access."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .room import RoomConfig
    from .solid_client import SolidClient
    from .messaging import Message

def mirror_room_to_pod(room: RoomConfig, source_client: SolidClient, mirror_client: SolidClient, since: Optional[int] = None) -> None:
    """Copy all accessible messages from a federated room to the local Pod mirror.
    
    Parameters
    ----------
    room : RoomConfig
        The target room configuration.
    source_client : SolidClient
        Client authenticated to read the room on the owner's Pod.
    mirror_client : SolidClient
        Client authenticated to write to the user's Pod.
    """
    from .room import read_room
    
    # Read messages from the source room
    messages = read_room(room, source_client, limit=1000)
    
    # Target mirroring path
    mirror_root = f"stash://mirrors/{room.room_id}/"
    messages_path = f"{mirror_root}messages/"
    
    # Write room metadata
    metadata = {
        "room_id": room.room_id,
        "name": room.name,
        "owner_webid": room.owner_webid,
        "pod_url": room.pod_url,
    }
    mirror_client.put(f"{mirror_root}room.json", json.dumps(metadata).encode("utf-8"))
    
    # Copy messages
    for msg in messages:
        if since is not None and msg.timestamp < since:
            continue
            
        msg_path = f"{messages_path}{msg.message_id}.json"
        msg_data = {
            "message_id": msg.message_id,
            "cert_id": msg.cert_id,
            "from_pub_hex": msg.from_pub_hex,
            "content": msg.content,
            "timestamp": msg.timestamp,
            "signature": msg.signature,
            "reply_to_id": msg.reply_to_id,
            "message_type": getattr(msg, "message_type", "text"),
        }
        # In a real implementation we'd check if it exists first, but PUT overwrites
        mirror_client.put(msg_path, json.dumps(msg_data).encode("utf-8"))

def get_mirror_messages(room_id: str, mirror_client: SolidClient) -> List[Message]:
    """Retrieve mirrored messages for a room from the local Pod.
    
    Parameters
    ----------
    room_id : str
        The ID of the mirrored room.
    mirror_client : SolidClient
        Client authenticated to read from the user's Pod.
    
    Returns
    -------
    List[Message]
        Messages found in the local mirror.
    """
    from .messaging import Message
    
    messages_path = f"stash://mirrors/{room_id}/messages/"
    try:
        listings = mirror_client.list_resources(messages_path)
    except Exception:
        return []
        
    messages = []
    for item in listings:
        if not item.endswith(".json"):
            continue
        try:
            raw = mirror_client.get(f"{messages_path}{item}")
            data = json.loads(raw.decode("utf-8"))
            msg = Message(
                message_id=data["message_id"],
                cert_id=data.get("cert_id", room_id),
                from_pub_hex=data["from_pub_hex"],
                content=data["content"],
                timestamp=data["timestamp"],
                signature=data["signature"],
                reply_to_id=data.get("reply_to_id"),
                message_type=data.get("message_type", "text"),
            )
            messages.append(msg)
        except Exception:
            pass
            
    # Sort chronologically
    messages.sort(key=lambda m: m.timestamp)
    return messages
