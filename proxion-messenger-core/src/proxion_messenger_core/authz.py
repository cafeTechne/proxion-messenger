"""Centralized authorization helpers for ProxionGateway."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .persist import AgentState
    from .local_store import LocalStore


def is_gateway_owner(agent: "AgentState", webid: str) -> bool:
    """Return True if *webid* is the gateway's identity DID."""
    from .didkey import pub_key_to_did
    try:
        return pub_key_to_did(agent.identity_pub_bytes) == webid
    except Exception:
        return False


def is_room_member(
    store: Optional["LocalStore"],
    local_rooms: dict,
    room_id: str,
    webid: str,
    websocket: Any = None,
) -> bool:
    """Return True if *webid* (or *websocket*) is a member of *room_id*."""
    room = local_rooms.get(room_id)
    if room is not None:
        if websocket is not None and websocket in room.get("members", set()):
            return True
        if webid and any(True for _ in []):
            pass
    if store and room_id and webid:
        try:
            members = store.get_room_members(room_id)
            if webid in members:
                return True
        except Exception:
            pass
    return False


def is_room_owner(
    store: Optional["LocalStore"],
    local_rooms: dict,
    room_id: str,
    webid: str,
) -> bool:
    """Return True if *webid* is the owner/creator of *room_id*."""
    room = local_rooms.get(room_id)
    if room is not None and room.get("creator_webid") == webid:
        return True
    if store and room_id and webid:
        try:
            role = store.get_room_role(room_id, webid)
            if role == "owner":
                return True
        except Exception:
            pass
    return False


def is_dm_participant(
    store: Optional["LocalStore"],
    thread_id: str,
    webid: str,
) -> bool:
    """Return True if *webid* is a participant in DM thread *thread_id*."""
    if not store or not thread_id or not webid:
        return False
    try:
        threads = store.get_dm_threads(owner_webid=webid)
        return any(t["thread_id"] == thread_id for t in threads)
    except Exception:
        return False
