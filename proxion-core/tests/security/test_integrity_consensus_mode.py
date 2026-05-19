"""Tests for cross-node integrity consensus (R15)."""
import os
import pytest

from proxion_messenger_core.integrity_consensus import (
    build_integrity_digest,
    evaluate_consensus,
    is_consensus_enabled,
    CONSENSUS_MISMATCH_WARNING,
    CONSENSUS_MISMATCH_CRITICAL,
)


class TestConsensusProbDetectsMismatch:
    def test_consensus_probe_detects_digest_mismatch(self):
        local = build_integrity_digest("policy-hash-A", "runtime-hash-A", "prov-hash-A")
        peer = build_integrity_digest("policy-hash-B", "runtime-hash-A", "prov-hash-A")
        result = evaluate_consensus(local, [peer])
        assert result["classification"] != "consensus_ok"
        assert result["disagreeing_peers"] == 1

    def test_identical_digests_produce_ok(self):
        local = build_integrity_digest("p", "r", "v")
        peer = build_integrity_digest("p", "r", "v")
        result = evaluate_consensus(local, [peer])
        assert result["classification"] == "consensus_ok"
        assert result["disagreeing_peers"] == 0

    def test_mismatch_detail_lists_differing_fields(self):
        local = build_integrity_digest("p-local", "r-local", "v-local")
        peer = build_integrity_digest("p-peer", "r-local", "v-peer")
        result = evaluate_consensus(local, [peer])
        mismatch = result["mismatches"][0]
        assert "policy_hash" in mismatch["fields"]
        assert "provenance_hash" in mismatch["fields"]
        assert "runtime_integrity_hash" not in mismatch["fields"]


class TestQuorumRules:
    def test_quorum_rules_classify_warning_vs_critical(self):
        local = build_integrity_digest("p", "r", "v")
        peers_agree = [build_integrity_digest("p", "r", "v") for _ in range(4)]
        peers_disagree = [build_integrity_digest("p-bad", "r", "v") for _ in range(6)]
        all_peers = peers_agree + peers_disagree
        result = evaluate_consensus(local, all_peers)
        assert result["classification"] == CONSENSUS_MISMATCH_CRITICAL

    def test_minority_mismatch_is_warning(self):
        local = build_integrity_digest("p", "r", "v")
        peers = [build_integrity_digest("p", "r", "v") for _ in range(9)]
        peers.append(build_integrity_digest("p-bad", "r", "v"))
        result = evaluate_consensus(local, peers)
        assert result["classification"] == CONSENSUS_MISMATCH_WARNING

    def test_no_peers_returns_ok(self):
        local = build_integrity_digest("p", "r", "v")
        result = evaluate_consensus(local, [])
        assert result["classification"] == "consensus_ok"
        assert result["total_peers"] == 0


class TestConsensuDisabledMode:
    def test_consensus_disabled_mode_no_probe(self, monkeypatch):
        monkeypatch.delenv("PROXION_ENABLE_INTEGRITY_CONSENSUS", raising=False)
        assert is_consensus_enabled() is False

    def test_consensus_enabled_when_env_set(self, monkeypatch):
        monkeypatch.setenv("PROXION_ENABLE_INTEGRITY_CONSENSUS", "1")
        assert is_consensus_enabled() is True
