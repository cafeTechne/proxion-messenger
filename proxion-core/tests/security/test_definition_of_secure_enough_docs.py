"""Tests verifying the DoSE documentation requirements (R15)."""
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).parents[3]
DOSE_DOC = REPO_ROOT / "docs" / "security" / "definition_of_secure_enough.md"
CONTROL_BASELINE_DOC = REPO_ROOT / "docs" / "security" / "control_baseline_v1.md"


class TestDoSEDocContainsRequiredSections:
    def test_dose_doc_contains_required_sections(self):
        assert DOSE_DOC.exists(), f"DoSE doc not found at {DOSE_DOC}"
        content = DOSE_DOC.read_text()
        assert "Top Risk Register" in content, "Must have Top Risk Register section"
        assert "Control Baseline" in content, "Must have Control Baseline section"
        assert "Security SLOs" in content, "Must have Security SLOs section"
        assert "Validation Evidence" in content, "Must have Validation Evidence section"
        assert "Stop Rule" in content, "Must have Stop Rule section"

    def test_control_baseline_doc_contains_all_must_have_controls(self):
        assert CONTROL_BASELINE_DOC.exists(), f"Control baseline doc not found at {CONTROL_BASELINE_DOC}"
        content = CONTROL_BASELINE_DOC.read_text()
        for ctrl in ["Identity", "Replay", "Revocation", "Audit", "Backup", "Containment"]:
            assert ctrl in content, f"Control baseline must mention '{ctrl}'"

    def test_stop_rule_present_and_unambiguous(self):
        assert DOSE_DOC.exists()
        content = DOSE_DOC.read_text()
        assert "Stop Rule" in content
        assert "30" in content, "Stop Rule must reference the 30-day window"
        assert "pause" in content.lower() or "stop" in content.lower()

    def test_escalation_rule_present(self):
        assert DOSE_DOC.exists()
        content = DOSE_DOC.read_text()
        assert "Escalation Rule" in content or "escalation" in content.lower()

    def test_risk_register_has_status_column(self):
        assert DOSE_DOC.exists()
        content = DOSE_DOC.read_text()
        assert any(s in content for s in ("mitigated", "accepted", "deferred"))
