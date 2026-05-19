"""Tests for weighted consensus evaluation (R16)."""
import time
import pytest
from proxion_messenger_core.integrity_consensus import (
    evaluate_weighted_consensus,
    apply_consensus_action_policy,
    TRUST_CORE,
    TRUST_EXTENDED,
    TRUST_OBSERVER,
    CONSENSUS_MISMATCH_WARNING,
    CONSENSUS_MISMATCH_CRITICAL,
)


def _digest(policy="hash-a", runtime="hash-b", provenance="hash-c", trust_class=TRUST_EXTENDED, age_s=0):
    return {
        "policy_hash": policy,
        "runtime_integrity_hash": runtime,
        "provenance_hash": provenance,
        "trust_class": trust_class,
        "generated_at": time.time() - age_s,
    }


LOCAL = _digest()


def test_weighted_quorum_classifies_warning_vs_critical():
    # One extended peer disagrees — warning (weight 1 of 1+1=2 total? No, 1 of 1 = 100%)
    # Use 3 agreeing peers (weight 3 each = 9) + 1 disagreeing (weight 1) → 1/10 = 10% → warning
    peers = [
        _digest(trust_class=TRUST_CORE),       # agrees, weight 3
        _digest(trust_class=TRUST_CORE),       # agrees, weight 3
        _digest(trust_class=TRUST_CORE),       # agrees, weight 3
        _digest(policy="different", trust_class=TRUST_EXTENDED),  # disagrees, weight 1
    ]
    result = evaluate_weighted_consensus(LOCAL, peers)
    assert result["classification"] == CONSENSUS_MISMATCH_WARNING

    # Flip: 1 agreeing core + 2 disagreeing core → 6/9 > 50% → critical
    peers_critical = [
        _digest(trust_class=TRUST_CORE),                           # agrees, weight 3
        _digest(policy="diff", trust_class=TRUST_CORE),            # disagrees, weight 3
        _digest(policy="diff", trust_class=TRUST_CORE),            # disagrees, weight 3
    ]
    result_critical = evaluate_weighted_consensus(LOCAL, peers_critical)
    assert result_critical["classification"] == CONSENSUS_MISMATCH_CRITICAL


def test_stale_peers_excluded():
    stale_peer = _digest(policy="different", trust_class=TRUST_CORE, age_s=700)  # > 600s timeout
    result = evaluate_weighted_consensus(LOCAL, [stale_peer], stale_timeout_s=600)
    assert result["excluded_stale"] == 1
    # With no active peers, total_weight == 0 → consensus_ok
    assert result["classification"] == "consensus_ok"


def test_action_policy_applies_proportional_response(tmp_path):
    from proxion_messenger_core.local_store import LocalStore
    from proxion_messenger_core.security_policy import reload_policy, get_policy, TIER_NORMAL, TIER_RESTRICTIVE
    reload_policy()
    store = LocalStore(str(tmp_path / "test.db"))

    # Observer mismatch → warning only, no tier change
    action = apply_consensus_action_policy(
        CONSENSUS_MISMATCH_CRITICAL, trust_class=TRUST_OBSERVER, store=store
    )
    assert action == "warning_emitted"
    assert get_policy().get_tier() == TIER_NORMAL

    # Core critical → tier escalation
    action = apply_consensus_action_policy(
        CONSENSUS_MISMATCH_CRITICAL, trust_class=TRUST_CORE, store=store
    )
    assert action == "tier_escalated"
    assert get_policy().get_tier() >= TIER_RESTRICTIVE
    reload_policy()
