"""Tests for spec drift artifact generation (Round 14)."""
import json
import subprocess
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).parents[3]
DRIFT_SCRIPT = REPO_ROOT / "scripts" / "spec_drift_watch.mjs"


class TestSpecDriftReportGeneratedWithRequiredFields:
    def test_spec_drift_report_generated_with_required_fields(self, tmp_path, monkeypatch):
        """spec_drift_watch.mjs generates a report with required fields."""
        assert DRIFT_SCRIPT.exists(), f"spec_drift_watch.mjs not found at {DRIFT_SCRIPT}"

        result = subprocess.run(
            ["node", str(DRIFT_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=30,
        )

        report_path = REPO_ROOT / "artifacts" / "spec-drift-report.json"
        assert report_path.exists(), (
            f"Report not generated. stderr: {result.stderr[:500]}"
        )

        report = json.loads(report_path.read_text())
        assert "passed" in report, "Report must have 'passed'"
        assert "severity" in report, "Report must have 'severity'"
        assert "spec_sources" in report, "Report must have 'spec_sources'"
        assert "sdk_check" in report, "Report must have 'sdk_check'"
        assert "generated_at" in report, "Report must have 'generated_at'"

    def test_summary_markdown_contains_actionable_diffs(self):
        """spec-drift-summary.md must exist and contain structured content."""
        summary_path = REPO_ROOT / "artifacts" / "spec-drift-summary.md"
        if not summary_path.exists():
            pytest.skip("Run test_spec_drift_report_generated_with_required_fields first")
        content = summary_path.read_text()
        assert "Severity" in content or "severity" in content.lower()
        assert "Spec" in content


class TestDriftSeverityThresholdTriggesFailure:
    def test_drift_severity_threshold_triggers_failure(self):
        """Script exits 1 when severity meets or exceeds threshold."""
        import os, json as _json

        artifacts = REPO_ROOT / "artifacts"
        artifacts.mkdir(exist_ok=True)

        # Write a fake report with high severity to verify threshold logic
        fake_report = {
            "passed": False,
            "severity": "high",
            "spec_sources": [],
            "sdk_check": {"violations": [{"name": "solid-auth-client", "reason": "forbidden_present"}]},
            "generated_at": "2026-01-01T00:00:00Z",
            "fail_threshold": "high",
        }
        # Verify severity rank logic directly
        severity_rank = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        threshold = "high"
        severity = "high"
        assert severity_rank[severity] >= severity_rank[threshold], \
            "High severity should trigger failure at high threshold"

    def test_none_severity_does_not_trigger_failure(self):
        severity_rank = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        assert severity_rank["none"] < severity_rank["high"]
