"""Tests for Solid SDK dependency policy enforcement."""
import json
import subprocess
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).parents[3]  # proxion-messenger/
WEB_PKG = REPO_ROOT / "web" / "package.json"
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_solid_sdk_versions.mjs"


def _load_pkg():
    return json.loads(WEB_PKG.read_text(encoding="utf-8"))


def test_required_inrupt_packages_present_and_pinned():
    """All required Inrupt packages must be declared in package.json."""
    pkg = _load_pkg()
    policy = pkg["proxion"]["solidSdkPolicy"]
    all_declared = {
        **pkg.get("dependencies", {}),
        **pkg.get("devDependencies", {}),
    }
    required = policy["required"]
    assert required, "required list must not be empty"
    for package in required:
        assert package in all_declared, f"Required package {package!r} not in package.json"
        version = all_declared[package]
        assert version, f"Package {package!r} must have a non-empty version"


def test_forbidden_legacy_solid_packages_blocked():
    """Forbidden legacy auth packages must be listed in the policy blocklist."""
    pkg = _load_pkg()
    policy = pkg["proxion"]["solidSdkPolicy"]
    forbidden = set(policy.get("forbidden", []))
    assert "solid-auth-client" in forbidden, "solid-auth-client must be in forbidden list"
    assert "solid-auth-fetcher" in forbidden, "solid-auth-fetcher must be in forbidden list"

    # Verify none of the forbidden packages appear in dependencies
    all_declared = {
        **pkg.get("dependencies", {}),
        **pkg.get("devDependencies", {}),
    }
    for bad in forbidden:
        assert bad not in all_declared, (
            f"Forbidden package {bad!r} found in package.json — remove it or use the override"
        )


def test_check_script_emits_machine_readable_report():
    """check_solid_sdk_versions.mjs must exist and produce valid JSON output."""
    assert CHECK_SCRIPT.exists(), f"check script not found at {CHECK_SCRIPT}"

    # Run it — must exit 0 (policy currently satisfied)
    result = subprocess.run(
        [sys.executable.replace("python", "node") if False else "node",
         str(CHECK_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT / "web"),
    )

    # The script writes artifacts/solid-sdk-check.json
    report_path = REPO_ROOT / "artifacts" / "solid-sdk-check.json"
    assert report_path.exists(), (
        f"Report file not created at {report_path}. Script stderr: {result.stderr[:500]}"
    )

    report = json.loads(report_path.read_text())
    assert "passed" in report, "Report must have a 'passed' field"
    assert "required" in report, "Report must have a 'required' field"
    assert "forbidden" in report, "Report must have a 'forbidden' field"
    assert "violations" in report, "Report must have a 'violations' field"
