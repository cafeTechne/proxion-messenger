"""Federation peer registry for tracking and discovering remote agents.

Stores peer records in the stash with federation metadata (DID, pod URL,
trust status, last seen timestamp).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .stash import StashClient


def _did_key(did: str) -> str:
    """Convert a DID to a filesystem-safe stash key.
    
    Parameters
    ----------
    did : str
        DID string (e.g., "did:key:z6Mk...").
    
    Returns
    -------
    str
        Safe stash key (e.g., "peers/did_key_z6Mk....json").
    """
    safe = did.replace(":", "_").replace("/", "_")
    return f"peers/{safe}.json"


@dataclass
class PeerRecord:
    """A peer in the federation network."""

    did: str
    pod_url: str
    display_name: str = ""
    last_seen_iso: str = ""
    trusted: bool = False

    def __post_init__(self):
        """Initialize last_seen_iso if not provided."""
        if not self.last_seen_iso:
            self.last_seen_iso = datetime.now(timezone.utc).isoformat()


async def register_peer(
    stash,
    did: str,
    pod_url: str,
    display_name: str = "",
    trusted: bool = False,
) -> PeerRecord:
    """Register or update a peer record.

    Parameters
    ----------
    stash : StashClient
        Stash client for persistence.
    did : str
        DID of the peer.
    pod_url : str
        Pod URL of the peer.
    display_name : str
        Optional display name for the peer.
    trusted : bool
        Whether this peer is trusted (default False).

    Returns
    -------
    PeerRecord
        The registered/updated peer record.
    """
    rec = PeerRecord(
        did=did,
        pod_url=pod_url,
        display_name=display_name,
        trusted=trusted,
    )
    key = _did_key(did)
    await stash.put(key, json.dumps(asdict(rec)).encode())
    return rec


async def get_peer(stash, did: str) -> Optional[PeerRecord]:
    """Retrieve a peer record by DID.

    Parameters
    ----------
    stash : StashClient
        Stash client for reading.
    did : str
        DID of the peer to retrieve.

    Returns
    -------
    Optional[PeerRecord]
        The peer record if found, None otherwise.
    """
    key = _did_key(did)
    try:
        data = await stash.get(key)
        record_dict = json.loads(data.decode())
        return PeerRecord(**record_dict)
    except Exception:
        return None


async def list_peers(stash, trusted_only: bool = False) -> list[PeerRecord]:
    """List all peer records.

    Parameters
    ----------
    stash : StashClient
        Stash client for reading.
    trusted_only : bool
        If True, return only trusted peers (default False).

    Returns
    -------
    list[PeerRecord]
        List of peer records.
    """
    records = []
    
    try:
        keys = await stash.list("peers/")
    except Exception:
        return []

    for key in keys:
        try:
            data = await stash.get(key)
            record_dict = json.loads(data.decode())
            rec = PeerRecord(**record_dict)
            
            if not trusted_only or rec.trusted:
                records.append(rec)
        except Exception:
            # Skip malformed records
            pass

    return records


async def remove_peer(stash, did: str) -> bool:
    """Remove a peer record.

    Parameters
    ----------
    stash : StashClient
        Stash client for deletion.
    did : str
        DID of the peer to remove.

    Returns
    -------
    bool
        True if the record existed and was deleted, False otherwise.
    """
    key = _did_key(did)
    try:
        await stash.delete(key)
        return True
    except Exception:
        return False


async def touch_peer(stash, did: str) -> Optional[PeerRecord]:
    """Update a peer's last_seen_iso timestamp to now.

    Parameters
    ----------
    stash : StashClient
        Stash client for reading/writing.
    did : str
        DID of the peer to touch.

    Returns
    -------
    Optional[PeerRecord]
        The updated peer record if found, None otherwise.
    """
    rec = await get_peer(stash, did)
    if rec is None:
        return None

    rec.last_seen_iso = datetime.now(timezone.utc).isoformat()
    key = _did_key(did)
    await stash.put(key, json.dumps(asdict(rec)).encode())
    return rec
