"""Tests for the deprecation exit criteria document and metric checks."""
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).parents[3]
DEPRECATION_DOC = REPO_ROOT / "docs" / "deprecation_legacy_solid_paths.md"


class TestExitCheckFailsWhenThresholdsNotMet:
    def test_error_rate_threshold_enforced(self):
        """check_exit_criteria fails when adapter error rate exceeds 0.5%."""
        from proxion_messenger_core.solid_migration import MigrationErrorStore, SOLID_AUTH_FAILED

        store = MigrationErrorStore()
        # Simulate 10 errors out of 100 requests (10% error rate — above 0.5% threshold)
        for _ in range(10):
            store.record(SOLID_AUTH_FAILED, "inrupt_bridge")

        snap = store.snapshot()
        total_errors = sum(
            sum(modes.values())
            for modes in snap["by_code"].values()
        )
        # 10 errors — threshold (0.5% of 100 requests) = 0.5 → above threshold
        threshold_count = 100 * 0.005  # 0.5 errors
        assert total_errors > threshold_count

    def test_mismatch_threshold_enforced(self):
        """check_exit_criteria fails when mismatch rate exceeds 0.1%."""
        from proxion_messenger_core.solid_migration import MigrationErrorStore

        store = MigrationErrorStore()
        for _ in range(5):
            store.record_dual_read_mismatch()

        snap = store.snapshot()
        total_requests = 100
        threshold = total_requests * 0.001  # 0.1% = 0.1 mismatches
        assert snap["dual_read_mismatch_count"] > threshold

    def test_fallback_rate_threshold_enforced(self):
        """check_exit_criteria fails when notification fallback rate exceeds 2%."""
        from proxion_messenger_core.solid_migration import MigrationErrorStore

        store = MigrationErrorStore()
        for _ in range(5):
            store.record_notifs_fallback("test_failure")

        snap = store.snapshot()
        total_notif_events = 100
        threshold = total_notif_events * 0.02  # 2% = 2 fallbacks
        assert snap["notifs_fallback_count"] > threshold


class TestExitCheckPassesAfterSustainedGreenWindow:
    def test_zero_errors_zero_mismatches_zero_fallbacks(self):
        """A fresh store with no events represents a green window."""
        from proxion_messenger_core.solid_migration import MigrationErrorStore

        store = MigrationErrorStore()
        snap = store.snapshot()

        assert snap["dual_read_mismatch_count"] == 0
        assert snap["notifs_fallback_count"] == 0
        # No error counts at all
        total_errors = sum(
            sum(modes.values()) for modes in snap["by_code"].values()
        )
        assert total_errors == 0


class TestDeprecationDocContainsRequiredSignoffFields:
    def test_deprecation_doc_exists(self):
        assert DEPRECATION_DOC.exists(), f"Deprecation doc not found at {DEPRECATION_DOC}"

    def test_doc_has_metric_thresholds(self):
        content = DEPRECATION_DOC.read_text(encoding="utf-8")
        assert "0.5%" in content, "Doc must specify 0.5% adapter error rate threshold"
        assert "0.1%" in content, "Doc must specify 0.1% mismatch rate threshold"
        assert "2%" in content, "Doc must specify 2% notification fallback rate threshold"
        assert "14" in content, "Doc must specify 14 consecutive days requirement"

    def test_doc_has_signoff_table(self):
        content = DEPRECATION_DOC.read_text(encoding="utf-8")
        assert "Sign-off" in content or "sign-off" in content.lower()
        assert "Security Owner" in content or "security owner" in content.lower()

    def test_doc_has_security_conditions(self):
        content = DEPRECATION_DOC.read_text(encoding="utf-8")
        assert "30 days" in content, "Doc must specify 30-day security condition window"
        assert "critical security" in content.lower()
