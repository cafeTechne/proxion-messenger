"""Tests for get_security_summary owner-only command (Round 6)."""
import pytest


class TestSecuritySummaryRollups:
    def test_security_summary_hours_bounds_enforced(self, tmp_path):
        from proxion_messenger_core.local_store import LocalStore
        store = LocalStore(str(tmp_path / "test.db"))

        # Test hour bounds: 1 to 168 (7 days)
        summary_min = store.get_security_summary(hours=0)
        assert summary_min["hours"] == 1  # Minimum enforced

        summary_max = store.get_security_summary(hours=999)
        assert summary_max["hours"] == 168  # Maximum enforced

        summary_valid = store.get_security_summary(hours=24)
        assert summary_valid["hours"] == 24

    def test_security_summary_contains_expected_rollup_keys(self, tmp_path):
        from proxion_messenger_core.local_store import LocalStore
        store = LocalStore(str(tmp_path / "test2.db"))

        summary = store.get_security_summary(hours=24)

        # Verify all expected keys are present
        expected_keys = {
            "hours",
            "rate_limits_triggered",
            "schema_rejects",
            "relay_replay_rejects",
            "auth_lockouts",
            "webhook_failures",
        }
        for key in expected_keys:
            assert key in summary, f"Missing key: {key}"

    def test_security_summary_default_hours(self, tmp_path):
        from proxion_messenger_core.local_store import LocalStore
        store = LocalStore(str(tmp_path / "test3.db"))

        summary = store.get_security_summary()
        assert summary["hours"] == 24  # Default should be 24

    def test_security_summary_all_counters_non_negative(self, tmp_path):
        from proxion_messenger_core.local_store import LocalStore
        store = LocalStore(str(tmp_path / "test4.db"))

        summary = store.get_security_summary(hours=24)

        # All counters should be non-negative
        assert summary["rate_limits_triggered"] >= 0
        assert summary["schema_rejects"] >= 0
        assert summary["relay_replay_rejects"] >= 0
        assert summary["auth_lockouts"] >= 0
        assert summary["webhook_failures"] >= 0
