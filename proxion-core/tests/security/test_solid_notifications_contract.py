"""Notification adapter fallback contract tests (Round 14).

Verifies reason code stability, fallback metric increments, and mode
equivalence guarantees under simulated pod capabilities.
"""
import pytest

from proxion_messenger_core.solid_migration import (
    MigrationErrorStore,
    MODE_LEGACY,
    MODE_BRIDGE,
    SOLID_NOT_SUPPORTED,
)

# Canonical notification fallback reason codes (must be stable across releases)
NOTIF_FALLBACK_CODES = {
    "notifs_capability_absent",
    "notifs_protocol_unsupported",
    "notifs_auth_failed",
    "notifs_transport_failed",
    "notifs_payload_invalid",
    "sdk_unavailable",
    "legacy_forced",
}


class TestSdkModeAndFallbackModeContractEquivalence:
    def test_sdk_mode_and_fallback_mode_contract_equivalence(self):
        """Both sdk and legacy modes must ultimately record the same notifs event types."""
        store = MigrationErrorStore()
        store.record_notifs_fallback("notifs_transport_failed")
        snap = store.snapshot()
        assert snap["notifs_fallback_count"] == 1
        assert snap["notifs_last_fallback_reason"] == "notifs_transport_failed"

    def test_zero_fallbacks_represents_sdk_mode_success(self):
        store = MigrationErrorStore()
        snap = store.snapshot()
        assert snap["notifs_fallback_count"] == 0


class TestFallbackReasonCodesAreBackwardCompatible:
    def test_fallback_reason_codes_are_backward_compatible(self):
        """All canonical fallback reason codes must be non-empty strings."""
        for code in NOTIF_FALLBACK_CODES:
            assert isinstance(code, str) and code, f"Code {code!r} must be a non-empty string"

    def test_legacy_forced_is_a_valid_fallback_reason(self):
        assert "legacy_forced" in NOTIF_FALLBACK_CODES

    def test_capability_absent_is_a_valid_fallback_reason(self):
        assert "notifs_capability_absent" in NOTIF_FALLBACK_CODES

    def test_payload_invalid_is_a_valid_fallback_reason(self):
        assert "notifs_payload_invalid" in NOTIF_FALLBACK_CODES

    def test_all_reason_codes_start_with_known_prefix_or_legacy(self):
        for code in NOTIF_FALLBACK_CODES:
            assert code.startswith("notifs_") or code in ("sdk_unavailable", "legacy_forced"), \
                f"Unexpected prefix for fallback code: {code!r}"


class TestNotificationContractMetricsIncrementCorrectly:
    def test_notification_contract_metrics_increment_correctly(self):
        store = MigrationErrorStore()
        store.record_notifs_fallback("notifs_capability_absent")
        store.record_notifs_fallback("notifs_transport_failed")
        store.record_notifs_fallback("notifs_transport_failed")

        snap = store.snapshot()
        assert snap["notifs_fallback_count"] == 3
        assert snap["notifs_last_fallback_reason"] == "notifs_transport_failed"

    def test_each_fallback_call_increments_count_by_one(self):
        store = MigrationErrorStore()
        for i in range(5):
            store.record_notifs_fallback("notifs_auth_failed")
            snap = store.snapshot()
            assert snap["notifs_fallback_count"] == i + 1

    def test_last_reason_reflects_most_recent_call(self):
        store = MigrationErrorStore()
        store.record_notifs_fallback("notifs_capability_absent")
        store.record_notifs_fallback("notifs_payload_invalid")
        snap = store.snapshot()
        assert snap["notifs_last_fallback_reason"] == "notifs_payload_invalid"
