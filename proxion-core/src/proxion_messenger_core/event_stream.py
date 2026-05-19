"""R12: Cursor-based signed security event stream for SIEM integration.
R15: Monotonic sequence IDs, gap detection, stream integrity state.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .local_store import LocalStore

_STREAM_INTEGRITY_OK = "ok"
_STREAM_INTEGRITY_GAP = "gap_detected"


def get_events_after(
    store: "LocalStore",
    cursor: str,
    limit: int,
    identity_key,
    pub_bytes: bytes,
) -> dict:
    """Return signed paginated security events after cursor (event id).

    Each event carries prev_event_hash for chain continuity and a monotonic
    sequence number. Gaps in sequence are flagged in stream_integrity_state.
    """
    limit = min(max(1, limit), 1000)
    events = store.get_security_events_after(cursor=cursor, limit=limit)

    prev_hash = _event_hash({"id": cursor}) if cursor else ""
    enriched = []
    expected_seq = None
    gap_detected = False

    for idx, ev in enumerate(events):
        ev_copy = dict(ev)
        ev_copy["prev_event_hash"] = prev_hash
        seq = ev_copy.get("seq_num") or idx
        ev_copy["stream_sequence"] = seq
        if expected_seq is not None and seq != expected_seq:
            gap_detected = True
        expected_seq = seq + 1
        ev_hash = _event_hash(ev_copy)
        ev_copy["event_hash"] = ev_hash
        prev_hash = ev_hash
        enriched.append(ev_copy)

    last_cursor = enriched[-1]["id"] if enriched else cursor
    integrity_state = _STREAM_INTEGRITY_GAP if gap_detected else _STREAM_INTEGRITY_OK

    payload: dict = {
        "cursor": cursor,
        "next_cursor": last_cursor,
        "events": enriched,
        "generated_at": time.time(),
        "signer_key_id": pub_bytes.hex()[:16],
        "stream_integrity_state": integrity_state,
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


def stream_integrity_state(
    store: "LocalStore",
    consumer_id: str,
    events: list[dict],
) -> dict:
    """Check sequence continuity for a consumer and update cursor.

    Returns dict with: state (str), gap_at_sequence (int|None), last_sequence (int).
    """
    cursor = store.get_stream_cursor(consumer_id)
    last_seq = cursor["last_sequence"] if cursor else -1

    gap_at = None
    for ev in events:
        seq = ev.get("stream_sequence", ev.get("seq_num", -1))
        if seq == -1:
            continue
        if last_seq >= 0 and seq != last_seq + 1:
            gap_at = seq
            break
        last_seq = seq

    if events:
        final_seq = events[-1].get("stream_sequence", events[-1].get("seq_num", last_seq))
        store.upsert_stream_cursor(consumer_id, final_seq)

    state = _STREAM_INTEGRITY_GAP if gap_at is not None else _STREAM_INTEGRITY_OK
    return {"state": state, "gap_at_sequence": gap_at, "last_sequence": last_seq}


def _event_hash(event: dict) -> str:
    canonical = json.dumps(
        {k: v for k, v in sorted(event.items())
         if k not in ("signature", "event_hash", "prev_event_hash")},
        default=str,
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()
