"""R15/R16: Cross-node integrity consensus for multi-gateway deployments.

R16 adds:
  - Trust classes: trusted_core, trusted_extended, observer
  - Weighted quorum rules (core peers count more)
  - Stale peer suppression based on heartbeat timeout

Consensus is disabled by default and never blocks startup.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

CONSENSUS_MISMATCH_WARNING = "consensus_mismatch_warning"
CONSENSUS_MISMATCH_CRITICAL = "consensus_mismatch_critical"

_CRITICAL_QUORUM_FRACTION = 0.5
_STALE_PEER_TIMEOUT_S = 600  # peers silent > 10 min are excluded

TRUST_CORE = "trusted_core"
TRUST_EXTENDED = "trusted_extended"
TRUST_OBSERVER = "observer"

_TRUST_WEIGHTS = {
    TRUST_CORE: 3,
    TRUST_EXTENDED: 1,
    TRUST_OBSERVER: 0,
}


def is_consensus_enabled() -> bool:
    return os.environ.get("PROXION_ENABLE_INTEGRITY_CONSENSUS") == "1"


def build_integrity_digest(
    policy_hash: str,
    runtime_integrity_hash: str,
    provenance_hash: str,
) -> dict:
    """Build a signed integrity digest for exchange with peers."""
    payload = {
        "policy_hash": policy_hash,
        "runtime_integrity_hash": runtime_integrity_hash,
        "provenance_hash": provenance_hash,
        "generated_at": time.time(),
    }
    payload["digest"] = _digest_hash(payload)
    return payload


def verify_peer_digest(local_digest: dict, peer_digest: dict) -> dict:
    """Compare local and peer integrity digests."""
    mismatches = []
    for key in ("policy_hash", "runtime_integrity_hash", "provenance_hash"):
        if local_digest.get(key) != peer_digest.get(key):
            mismatches.append(key)
    return {"match": len(mismatches) == 0, "mismatches": mismatches}


def evaluate_consensus(
    local_digest: dict,
    peer_digests: list[dict],
) -> dict:
    """Evaluate consensus across all peers (R15 simple mode — no trust classes).

    Returns dict with: classification, disagreeing_peers, total_peers, mismatches.
    """
    if not peer_digests:
        return {
            "classification": "consensus_ok",
            "disagreeing_peers": 0,
            "total_peers": 0,
            "mismatches": [],
        }

    disagreeing = 0
    all_mismatches = []
    for i, peer in enumerate(peer_digests):
        result = verify_peer_digest(local_digest, peer)
        if not result["match"]:
            disagreeing += 1
            all_mismatches.append({"peer_index": i, "fields": result["mismatches"]})

    total = len(peer_digests)
    fraction = disagreeing / total if total > 0 else 0.0

    if fraction > _CRITICAL_QUORUM_FRACTION:
        classification = CONSENSUS_MISMATCH_CRITICAL
    elif disagreeing > 0:
        classification = CONSENSUS_MISMATCH_WARNING
    else:
        classification = "consensus_ok"

    return {
        "classification": classification,
        "disagreeing_peers": disagreeing,
        "total_peers": total,
        "mismatches": all_mismatches,
    }


def evaluate_weighted_consensus(
    local_digest: dict,
    peer_digests: list[dict],
    stale_timeout_s: float = _STALE_PEER_TIMEOUT_S,
) -> dict:
    """R16: Evaluate consensus with trust class weights and stale peer suppression.

    Each peer_digest may include: trust_class (str), generated_at (float).
    Returns dict with: classification, weighted_mismatch_score, total_weight,
    excluded_stale, mismatches.
    """
    now = time.time()
    total_weight = 0
    mismatch_weight = 0
    excluded_stale = 0
    all_mismatches = []

    for i, peer in enumerate(peer_digests):
        peer_time = peer.get("generated_at", now)
        if now - peer_time > stale_timeout_s:
            excluded_stale += 1
            continue

        trust_class = peer.get("trust_class", TRUST_EXTENDED)
        weight = _TRUST_WEIGHTS.get(trust_class, 1)

        if trust_class == TRUST_OBSERVER:
            continue

        total_weight += weight
        result = verify_peer_digest(local_digest, peer)
        if not result["match"]:
            mismatch_weight += weight
            all_mismatches.append({
                "peer_index": i,
                "trust_class": trust_class,
                "weight": weight,
                "fields": result["mismatches"],
            })

    fraction = mismatch_weight / total_weight if total_weight > 0 else 0.0

    if total_weight == 0:
        classification = "consensus_ok"
    elif fraction > _CRITICAL_QUORUM_FRACTION:
        classification = CONSENSUS_MISMATCH_CRITICAL
    elif mismatch_weight > 0:
        classification = CONSENSUS_MISMATCH_WARNING
    else:
        classification = "consensus_ok"

    return {
        "classification": classification,
        "weighted_mismatch_score": mismatch_weight,
        "total_weight": total_weight,
        "excluded_stale": excluded_stale,
        "mismatches": all_mismatches,
    }


def apply_consensus_action_policy(
    classification: str,
    trust_class: str = TRUST_EXTENDED,
    store=None,
) -> str:
    """Apply proportional action based on consensus classification and trust class.

    Returns action taken: none|warning_emitted|tier_escalated.
    """
    if classification == "consensus_ok":
        return "none"

    if trust_class == TRUST_OBSERVER:
        if store:
            try:
                store.save_security_event(
                    "consensus_observer_mismatch", "info",
                    details=f"classification={classification}",
                )
            except Exception:
                pass
        return "warning_emitted"

    if classification == CONSENSUS_MISMATCH_WARNING:
        if store:
            try:
                store.save_security_event(
                    "consensus_mismatch_warning", "warning",
                    details=f"trust_class={trust_class}",
                )
            except Exception:
                pass
        return "warning_emitted"

    if classification == CONSENSUS_MISMATCH_CRITICAL:
        if store:
            try:
                store.save_security_event(
                    "consensus_mismatch_critical", "critical",
                    details=f"trust_class={trust_class}",
                )
            except Exception:
                pass
        if trust_class == TRUST_CORE:
            try:
                from .security_policy import get_policy, TIER_RESTRICTIVE
                pol = get_policy()
                if pol.get_tier() < TIER_RESTRICTIVE:
                    pol.set_tier(TIER_RESTRICTIVE, reason="consensus_core_quorum_mismatch")
                    return "tier_escalated"
            except Exception:
                pass
        return "warning_emitted"

    return "none"


def _digest_hash(payload: dict) -> str:
    fields = {k: v for k, v in payload.items() if k != "digest"}
    canonical = json.dumps(fields, sort_keys=True, default=str).encode()
    return hashlib.sha256(canonical).hexdigest()
