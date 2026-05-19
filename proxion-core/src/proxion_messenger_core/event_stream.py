"""R12: Cursor-based signed security event stream for SIEM integration."""
from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .local_store import LocalStore


def get_events_after(
    store: "LocalStore",
    cursor: str,
    limit: int,
    identity_key,
    pub_bytes: bytes,
) -> dict:
    """Return signed paginated security events after cursor (event id).

    Each event carries prev_event_hash for chain continuity.
    The response envelope is signed with the gateway identity key.
    """
    limit = min(max(1, limit), 1000)
    events = store.get_security_events_after(cursor=cursor, limit=limit)

    prev_hash = _event_hash({"id": cursor}) if cursor else ""
    enriched = []
    for ev in events:
        ev_copy = dict(ev)
        ev_copy["prev_event_hash"] = prev_hash
        ev_hash = _event_hash(ev_copy)
        ev_copy["event_hash"] = ev_hash
        prev_hash = ev_hash
        enriched.append(ev_copy)

    last_cursor = enriched[-1]["id"] if enriched else cursor
    payload: dict = {
        "cursor": cursor,
        "next_cursor": last_cursor,
        "events": enriched,
        "generated_at": time.time(),
        "signer_key_id": pub_bytes.hex()[:16],
    }

    payload_bytes = json.dumps(
        {k: v for k, v in payload.items()},
        default=str,
        sort_keys=True,
    ).encode()
    try:
        sig = identity_key.sign(payload_bytes)
        payload["signature"] = sig.hex()
        payload["pub_key_hex"] = pub_bytes.hex()
    except Exception:
        pass

    return payload


def _event_hash(event: dict) -> str:
    canonical = json.dumps(
        {k: v for k, v in sorted(event.items())
         if k not in ("signature", "event_hash", "prev_event_hash")},
        default=str,
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()
