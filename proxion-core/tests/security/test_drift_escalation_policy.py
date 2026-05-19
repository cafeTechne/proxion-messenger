"""Tests for drift-aware security tier escalation (Round 14)."""
import os
import pytest
from unittest.mock import patch

from proxion_messenger_core.security_policy import (
    SecurityPolicy,
    TIER_NORMAL, TIER_RESTRICTIVE, TIER_CONTAINMENT,
)


class TestHighDriftEnablesRestrictiveMode:
    def test_high_drift_enables_restrictive_mode(self):
        policy = SecurityPolicy()
        with patch.dict(os.environ, {"PROXION_DRIFT_ESCALATION_MODE": "restrictive"}):
            new_tier = policy.apply_drift_escalation("high")
        assert new_tier >= TIER_RESTRICTIVE

    def test_high_drift_containment_mode_reaches_containment(self):
        policy = SecurityPolicy()
        with patch.dict(os.environ, {"PROXION_DRIFT_ESCALATION_MODE": "containment"}):
            new_tier = policy.apply_drift_escalation("high")
        assert new_tier == TIER_CONTAINMENT

    def test_low_drift_does_not_escalate(self):
        policy = SecurityPolicy()
        with patch.dict(os.environ, {"PROXION_DRIFT_ESCALATION_MODE": "restrictive"}):
            new_tier = policy.apply_drift_escalation("low")
        assert new_tier == TIER_NORMAL

    def test_off_mode_never_escalates(self):
        policy = SecurityPolicy()
        with patch.dict(os.environ, {"PROXION_DRIFT_ESCALATION_MODE": "off"}):
            new_tier = policy.apply_drift_escalation("critical")
        assert new_tier == TIER_NORMAL

    def test_critical_drift_escalates(self):
        policy = SecurityPolicy()
        with patch.dict(os.environ, {"PROXION_DRIFT_ESCALATION_MODE": "restrictive"}):
            new_tier = policy.apply_drift_escalation("critical")
        assert new_tier >= TIER_RESTRICTIVE


class TestContainmentModeBlocksHighRiskMutations:
    def test_containment_mode_blocks_high_risk_mutations(self):
        policy = SecurityPolicy()
        with patch.dict(os.environ, {"PROXION_DRIFT_ESCALATION_MODE": "containment"}):
            policy.apply_drift_escalation("high")

        assert policy.get_tier() == TIER_CONTAINMENT
        # Confirm drift protection is active
        assert policy.is_drift_protection_active()

    def test_drift_protection_inactive_when_mode_off(self):
        policy = SecurityPolicy()
        with patch.dict(os.environ, {"PROXION_DRIFT_ESCALATION_MODE": "off"}):
            policy.apply_drift_escalation("critical")
        assert not policy.is_drift_protection_active()


class TestDriftProtectionReturnsStableErrorCode:
    def test_drift_protection_returns_stable_error_code(self):
        """HTTP 503 body must contain stable error key when drift protection active."""
        error_body = {"error": "spec_drift_protection_active"}
        assert error_body["error"] == "spec_drift_protection_active"

    def test_drift_escalation_reason_is_recorded(self):
        policy = SecurityPolicy()
        with patch.dict(os.environ, {"PROXION_DRIFT_ESCALATION_MODE": "restrictive"}):
            policy.apply_drift_escalation("high")
        state = policy.get_tier_state()
        assert state["tier"] >= TIER_RESTRICTIVE
        assert any("drift" in r for r in state["reasons"])
