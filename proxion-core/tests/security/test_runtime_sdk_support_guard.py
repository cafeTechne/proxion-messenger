"""Tests for runtime SDK support guard (Round 14)."""
import json
import os
import time
import tempfile
import pytest
from unittest.mock import MagicMock, patch

from proxion_messenger_core.sdk_support_guard import (
    check_sdk_support, enforce_sdk_support_guard, REQUIRED_PACKAGES,
)


def _make_pkg_json(tmp_path, deps=None, dev_deps=None, policy=None):
    pkg = {
        "name": "proxion-web",
        "dependencies": deps or {},
        "devDependencies": dev_deps or {},
        "proxion": {"solidSdkPolicy": policy or {"required": [], "forbidden": []}},
    }
    p = tmp_path / "package.json"
    p.write_text(json.dumps(pkg))
    return str(p)


@pytest.fixture
def full_pkg(tmp_path):
    deps = {name: "^2.0.0" for name in REQUIRED_PACKAGES}
    return _make_pkg_json(tmp_path, deps=deps)


@pytest.fixture
def empty_pkg(tmp_path):
    return _make_pkg_json(tmp_path, deps={})


class TestRuntimeSdkSupportGuard:
    def test_check_passes_with_all_required(self, full_pkg):
        result = check_sdk_support(full_pkg)
        assert result["ok"] is True
        assert result["missing_packages"] == []
        assert result["unsupported_packages"] == []
        assert result["policy_version"]

    def test_check_fails_with_missing_packages(self, empty_pkg):
        result = check_sdk_support(empty_pkg)
        assert result["ok"] is False
        assert len(result["missing_packages"]) > 0

    def test_check_reports_forbidden_packages(self, tmp_path):
        deps = {name: "^2.0.0" for name in REQUIRED_PACKAGES}
        deps["solid-auth-client"] = "^1.0.0"
        pkg = _make_pkg_json(tmp_path, deps=deps)
        result = check_sdk_support(pkg)
        assert result["ok"] is False
        assert "solid-auth-client" in result["unsupported_packages"]

    def test_runtime_fails_on_unsupported_sdk_when_required(self, empty_pkg):
        with patch.dict(os.environ, {"PROXION_REQUIRE_SUPPORTED_SOLID_SDK_RUNTIME": "1"}):
            with patch("proxion_messenger_core.sdk_support_guard._find_package_json",
                       return_value=None):
                with pytest.raises(RuntimeError, match="SDK support guard failed"):
                    enforce_sdk_support_guard()

    def test_runtime_allows_temporary_bypass_until_timestamp(self, empty_pkg):
        future = str(time.time() + 3600)
        with patch.dict(os.environ, {
            "PROXION_REQUIRE_SUPPORTED_SOLID_SDK_RUNTIME": "1",
            "PROXION_ALLOW_UNSUPPORTED_SDK_UNTIL": future,
        }):
            with patch("proxion_messenger_core.sdk_support_guard._find_package_json",
                       return_value=None):
                enforce_sdk_support_guard()  # should not raise

    def test_runtime_emits_security_event_on_bypass(self, empty_pkg):
        future = str(time.time() + 3600)
        mock_store = MagicMock()
        with patch.dict(os.environ, {
            "PROXION_REQUIRE_SUPPORTED_SOLID_SDK_RUNTIME": "1",
            "PROXION_ALLOW_UNSUPPORTED_SDK_UNTIL": future,
        }):
            with patch("proxion_messenger_core.sdk_support_guard._find_package_json",
                       return_value=None):
                enforce_sdk_support_guard(store=mock_store)
        mock_store.save_security_event.assert_called_once()
        call_args = mock_store.save_security_event.call_args
        assert call_args[0][0] == "sdk_support_guard_bypassed"
        assert call_args[0][1] == "critical"

    def test_expired_bypass_does_not_prevent_failure(self):
        past = str(time.time() - 10)
        with patch.dict(os.environ, {
            "PROXION_REQUIRE_SUPPORTED_SOLID_SDK_RUNTIME": "1",
            "PROXION_ALLOW_UNSUPPORTED_SDK_UNTIL": past,
        }):
            with patch("proxion_messenger_core.sdk_support_guard._find_package_json",
                       return_value=None):
                with pytest.raises(RuntimeError):
                    enforce_sdk_support_guard()

    def test_no_env_var_means_guard_is_noop(self):
        with patch.dict(os.environ, {}, clear=True):
            enforce_sdk_support_guard()  # should not raise even with no package.json
