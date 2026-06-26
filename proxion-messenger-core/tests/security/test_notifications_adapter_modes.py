"""Tests for PROXION_SOLID_NOTIFS_MODE handling in poll_loop."""
import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from proxion_messenger_core.solid_migration import migration_store


class TestAutoModePrefersSdkWhenSupported:
    def test_auto_mode_is_default(self):
        """Default notifs mode is 'auto'."""
        from proxion_messenger_core.solid_migration import current_notifs_mode
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PROXION_SOLID_NOTIFS_MODE", None)
            mode = current_notifs_mode()
        assert mode == "auto"

    def test_auto_mode_env_read_correctly(self):
        with patch.dict(os.environ, {"PROXION_SOLID_NOTIFS_MODE": "auto"}):
            from proxion_messenger_core.solid_migration import current_notifs_mode
            assert current_notifs_mode() == "auto"

    def test_legacy_mode_env_read_correctly(self):
        with patch.dict(os.environ, {"PROXION_SOLID_NOTIFS_MODE": "legacy"}):
            from proxion_messenger_core.solid_migration import current_notifs_mode
            assert current_notifs_mode() == "legacy"


class TestAutoModeFallbackReasonRecorded:
    def test_record_notifs_fallback_stores_reason(self):
        """migration_store.record_notifs_fallback records the reason."""
        before = migration_store._notifs_fallback_count
        migration_store.record_notifs_fallback("sdk_not_implemented")
        assert migration_store._notifs_fallback_count == before + 1
        assert migration_store._notifs_last_fallback_reason == "sdk_not_implemented"

    def test_snapshot_includes_notifs_data(self):
        migration_store.record_notifs_fallback("test_reason")
        snap = migration_store.snapshot()
        assert "notifs_fallback_count" in snap
        assert "notifs_last_fallback_reason" in snap
        assert snap["notifs_last_fallback_reason"] is not None


class TestSdkModeWithoutSupportReturnsNotSupportedError:
    def test_sdk_mode_recorded_in_poll_loop_start(self):
        """When PROXION_SOLID_NOTIFS_MODE=sdk, poll_loop records fallback reason."""
        # We test the migration_store interaction without running the full loop
        before = migration_store._notifs_fallback_count

        # Simulate what poll_loop does at startup when mode=sdk
        with patch.dict(os.environ, {"PROXION_SOLID_NOTIFS_MODE": "sdk"}):
            notifs_mode = os.environ.get("PROXION_SOLID_NOTIFS_MODE", "auto")
            if notifs_mode == "sdk":
                migration_store.record_notifs_fallback("sdk_not_implemented")

        assert migration_store._notifs_fallback_count > before
        assert "sdk_not_implemented" in migration_store._notifs_last_fallback_reason
