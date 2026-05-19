"""R15: Optional cross-node integrity consensus for multi-gateway deployments.

When PROXION_ENABLE_INTEGRITY_CONSENSUS=1, the gateway exchanges signed integrity
digests with trusted peers and classifies divergence as warning or critical based
on quorum fraction.

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

_CRITICAL_QUORUM_FRACTION = 0.5  # >50% disagreement → critical


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
    """Compare local and peer integrity digests.

    Returns dict with: match (bool), mismatches (list[str]).
    """
    mismatches = []
    for key in ("policy_hash", "runtime_integrity_hash", "provenance_hash"):
        if local_digest.get(key) != peer_digest.get(key):
            mismatches.append(key)
    return {"match": len(mismatches) == 0, "mismatches": mismatches}


def evaluate_consensus(
    local_digest: dict,
    peer_digests: list[dict],
) -> dict:
    """Evaluate consensus across all peers.

    Returns dict with:
      classification (str): consensus_ok | consensus_mismatch_warning | consensus_mismatch_critical
      disagreeing_peers (int)
      total_peers (int)
      mismatches (list)
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


def _digest_hash(payload: dict) -> str:
    fields = {k: v for k, v in payload.items() if k != "digest"}
    canonical = json.dumps(fields, sort_keys=True, default=str).encode()
    return hashlib.sha256(canonical).hexdigest()
