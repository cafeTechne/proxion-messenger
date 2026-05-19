"""R11: Non-destructive incident policy simulation harness.

Replays recent security_events through the current policy tier framework and
computes a hypothetical escalation timeline along with an impact summary.
"""
from __future__ import annotations

import time
from typing import Optional


def simulate_incident_policy(
    store,
    hours: int = 24,
    tier_profile: Optional[dict] = None,
) -> dict:
    """Replay the last *hours* of security events through policy tiers.

    Parameters
    ----------
    store:
        LocalStore instance.
    hours:
        How many hours back to source events (max 168 = 7 days).
    tier_profile:
        Optional override thresholds dict, e.g.:
        ``{"tier1_auth_lockouts": 2, "tier2_replay_rejects": 20}``.

    Returns
    -------
    dict with keys:
        - ``hours``: the hours window used
        - ``events_replayed``: total event count in window
        - ``escalation_timeline``: list of dicts describing hypothetical tier changes
        - ``blocked_actions``: count of commands that would have been blocked
        - ``false_positive_candidates``: list of event_types appearing ≥ 5 times
          while tier stayed at T0 (low-signal repeated noise)
        - ``final_tier``: hypothetical final tier
    """
    from .security_policy import (
        SecurityPolicy,
        TIER_NORMAL, TIER_ELEVATED, TIER_RESTRICTIVE, TIER_CONTAINMENT,
        _TIER1_AUTH_LOCKOUTS, _TIER1_SCHEMA_REJECTS,
        _TIER2_AUTH_LOCKOUTS, _TIER2_REPLAY_REJECTS,
        _TIER3_AUTH_LOCKOUTS, _TIER3_DB_INTEGRITY,
        _TIER2_BLOCKED_COMMANDS, _TIER3_BLOCKED_COMMANDS,
    )

    hours = min(max(1, int(hours)), 168)
    cutoff = time.time() - hours * 3600

    # Pull events
    try:
        events = store.get_security_events(limit=5000) if store else []
        events = [e for e in events if (e.get("created_at") or 0) >= cutoff]
    except Exception:
        events = []

    # Override thresholds if requested
    t1_auth = int((tier_profile or {}).get("tier1_auth_lockouts", _TIER1_AUTH_LOCKOUTS))
    t1_schema = int((tier_profile or {}).get("tier1_schema_rejects", _TIER1_SCHEMA_REJECTS))
    t2_auth = int((tier_profile or {}).get("tier2_auth_lockouts", _TIER2_AUTH_LOCKOUTS))
    t2_replay = int((tier_profile or {}).get("tier2_replay_rejects", _TIER2_REPLAY_REJECTS))
    t3_auth = int((tier_profile or {}).get("tier3_auth_lockouts", _TIER3_AUTH_LOCKOUTS))
    t3_db = int((tier_profile or {}).get("tier3_db_integrity", _TIER3_DB_INTEGRITY))

    # Replay events chronologically
    policy = SecurityPolicy()
    timeline = []
    blocked_actions = 0
    event_type_counts: dict[str, int] = {}

    for evt in sorted(events, key=lambda e: e.get("created_at", 0)):
        evt_type = evt.get("event_type", "")
        event_type_counts[evt_type] = event_type_counts.get(evt_type, 0) + 1

        # Map event type to signal
        auth = 1 if evt_type in ("auth_failed", "auth_lockout", "login_failed") else 0
        schema = 1 if evt_type in ("schema_rejected", "unknown_relay_fields") else 0
        replay = 1 if evt_type in ("relay_replay_detected", "replay_detected") else 0
        db_int = 1 if evt_type in ("db_integrity_failed",) else 0

        signals = {
            "auth_lockouts": auth,
            "schema_rejects": schema,
            "replay_rejects": replay,
            "db_integrity_events": db_int,
        }

        old_tier = policy.get_tier()

        # Apply custom thresholds inline
        new_tier = old_tier
        if db_int >= t3_db or auth >= t3_auth:
            new_tier = TIER_CONTAINMENT
        elif auth >= t2_auth or replay >= t2_replay:
            new_tier = max(new_tier, TIER_RESTRICTIVE)
        elif auth >= t1_auth or schema >= t1_schema:
            new_tier = max(new_tier, TIER_ELEVATED)

        if new_tier > old_tier:
            policy.set_tier(new_tier, reason=f"sim_{evt_type}")
            tier_names = ["normal", "elevated", "restrictive", "containment"]
            timeline.append({
                "at": evt.get("created_at"),
                "event_type": evt_type,
                "old_tier": old_tier,
                "new_tier": new_tier,
                "tier_name": tier_names[new_tier],
            })

        # Count blocked commands at this tier
        tier = policy.get_tier()
        if tier >= TIER_CONTAINMENT and "_TIER3_BLOCKED" in str(evt_type):
            blocked_actions += 1
        elif tier >= TIER_RESTRICTIVE and evt_type in _TIER2_BLOCKED_COMMANDS:
            blocked_actions += 1

    # False positive candidates: high-frequency events that never caused escalation
    false_positive_candidates = [
        et for et, cnt in event_type_counts.items()
        if cnt >= 5 and not any(t["event_type"] == et for t in timeline)
    ]

    return {
        "hours": hours,
        "events_replayed": len(events),
        "escalation_timeline": timeline,
        "blocked_actions": blocked_actions,
        "false_positive_candidates": false_positive_candidates,
        "final_tier": policy.get_tier(),
    }
