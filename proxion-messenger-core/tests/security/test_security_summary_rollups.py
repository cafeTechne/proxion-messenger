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

    def test_security_summary_counts_recorded_events(self, tmp_path):
        from proxion_messenger_core.local_store import LocalStore
        store = LocalStore(str(tmp_path / "test4.db"))

        counters = ("rate_limits_triggered", "schema_rejects", "relay_replay_rejects",
                    "auth_lockouts", "webhook_failures")
        empty = store.get_security_summary(hours=24)
        assert {k: empty[k] for k in counters} == dict.fromkeys(counters, 0)

        store.save_audit_log("rate_limit_exceeded", "warning")
        store.save_audit_log("relay_replay_rejected", "warning")
        store.save_audit_log("relay_replay_rejected", "warning")
        store.save_security_event("schema_reject", "info")
        store.save_security_event("auth_lockout", "warning")
        store.save_security_event("unrelated_event", "info")

        summary = store.get_security_summary(hours=24)
        assert {k: summary[k] for k in counters} == {
            "rate_limits_triggered": 1, "schema_rejects": 1, "relay_replay_rejects": 2,
            "auth_lockouts": 1, "webhook_failures": 0,
        }
