"""Room invitation codes for managing access to private rooms.

Invitation codes allow users to share limited-time or limited-use access to
private rooms without exposing direct URLs or requiring manual permission
granting for each user.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .stash import StashClient


@dataclass
class InviteRecord:
    """An invitation code for a room."""
    
    code: str
    room_id: str
    created_by_webid: str
    created_iso: str
    expires_iso: str
    max_uses: int = 0  # 0 = unlimited
    use_count: int = 0
    active: bool = True
    
    def __post_init__(self):
        """Initialize timestamps if not provided."""
        if not self.created_iso:
            self.created_iso = datetime.now(timezone.utc).isoformat()
        if not self.expires_iso:
            # Default: expires in 24 hours
            expires = datetime.now(timezone.utc) + timedelta(hours=24)
            self.expires_iso = expires.isoformat()


async def create_invite(
    stash,
    room_id: str,
    created_by_webid: str,
    expires_hours: int = 24,
    max_uses: int = 0,
) -> InviteRecord:
    """Create a new invitation code for a room.
    
    Parameters
    ----------
    stash : StashClient
        Stash client for persistence.
    room_id : str
        ID of the room to invite to.
    created_by_webid : str
        WebID of the user creating the invite.
    expires_hours : int
        Hours until the invite expires (default 24).
    max_uses : int
        Maximum number of times the invite can be used (0=unlimited).
    
    Returns
    -------
    InviteRecord
        The created invitation record.
    """
    code = secrets.token_urlsafe(12)
    
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=expires_hours)
    
    rec = InviteRecord(
        code=code,
        room_id=room_id,
        created_by_webid=created_by_webid,
        created_iso=now.isoformat(),
        expires_iso=expires.isoformat(),
        max_uses=max_uses,
        active=True,
    )
    
    key = f"invites/{code}.json"
    await stash.put(key, json.dumps(asdict(rec)).encode())
    
    return rec


async def get_invite(stash, code: str) -> Optional[InviteRecord]:
    """Retrieve an invitation record by code.
    
    Parameters
    ----------
    stash : StashClient
        Stash client for reading.
    code : str
        Invitation code.
    
    Returns
    -------
    Optional[InviteRecord]
        The invitation record if found, None otherwise.
    """
    key = f"invites/{code}.json"
    try:
        data = await stash.get(key)
        if not data:
            return None
        rec_dict = json.loads(data.decode())
        return InviteRecord(**rec_dict)
    except Exception:
        return None


async def use_invite(stash, code: str) -> Optional[InviteRecord]:
    """Use an invitation code, incrementing use count.
    
    Returns None if the invite is expired, inactive, or max_uses exceeded.
    
    Parameters
    ----------
    stash : StashClient
        Stash client for persistence.
    code : str
        Invitation code to use.
    
    Returns
    -------
    Optional[InviteRecord]
        The updated invitation record if successful, None if invalid/expired.
    """
    rec = await get_invite(stash, code)
    if not rec:
        return None
    
    # Check if active
    if not rec.active:
        return None
    
    # Check if expired
    expires_dt = datetime.fromisoformat(rec.expires_iso)
    if datetime.now(timezone.utc) > expires_dt:
        return None
    
    # Check if max_uses exceeded
    if rec.max_uses > 0 and rec.use_count >= rec.max_uses:
        return None
    
    # Increment use count
    rec.use_count += 1
    
    # Deactivate if max_uses reached
    if rec.max_uses > 0 and rec.use_count >= rec.max_uses:
        rec.active = False
    
    # Persist updated record
    key = f"invites/{code}.json"
    await stash.put(key, json.dumps(asdict(rec)).encode())
    
    return rec


async def revoke_invite(stash, code: str) -> bool:
    """Revoke an invitation code by marking it inactive.
    
    Parameters
    ----------
    stash : StashClient
        Stash client for persistence.
    code : str
        Invitation code to revoke.
    
    Returns
    -------
    bool
        True if revoked successfully, False if invite not found.
    """
    rec = await get_invite(stash, code)
    if not rec:
        return False
    
    rec.active = False
    key = f"invites/{code}.json"
    await stash.put(key, json.dumps(asdict(rec)).encode())
    
    return True


async def list_invites(
    stash,
    room_id: Optional[str] = None,
) -> list[InviteRecord]:
    """List all invitation records, optionally filtered by room.
    
    Parameters
    ----------
    stash : StashClient
        Stash client for reading.
    room_id : Optional[str]
        If provided, only return invites for this room.
    
    Returns
    -------
    list[InviteRecord]
        List of matching invitation records.
    """
    invites_keys = await stash.list("invites/")
    
    records = []
    for key in invites_keys:
        try:
            data = await stash.get(key)
            if data:
                rec_dict = json.loads(data.decode())
                rec = InviteRecord(**rec_dict)
                if room_id is None or rec.room_id == room_id:
                    records.append(rec)
        except Exception:
            continue
    
    return records
