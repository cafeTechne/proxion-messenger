"""Tests for the recovery drill runner (R16)."""
import time
import pytest
from proxion_messenger_core.recovery_drill_runner import (
    list_drill_templates,
    run_drill,
)


@pytest.fixture
def store(tmp_path):
    from proxion_messenger_core.local_store import LocalStore
    return LocalStore(str(tmp_path / "test.db"))


def test_templates_listed():
    templates = list_drill_templates()
    assert len(templates) >= 3
    ids = {t["id"] for t in templates}
    assert "compromised_key_rotation" in ids
    assert "restore_import_budget" in ids
    assert "degraded_mode_recovery" in ids


def test_drill_persists_pass_or_fail(store):
    result = run_drill("compromised_key_rotation", store=store, dry_run=False)
    assert result["status"] in ("pass", "fail")
    assert "drill_id" in result
    assert "findings" in result

    drills = store.get_drill_results_in_window(0, time.time() + 1)
    assert any(d["drill_id"] == result["drill_id"] for d in drills)


def test_drill_report_has_duration_and_findings(store):
    result = run_drill("degraded_mode_recovery", store=store, dry_run=True)
    assert "duration_seconds" in result
    assert isinstance(result["duration_seconds"], (int, float))
    assert result["duration_seconds"] >= 0
    assert isinstance(result["findings"], dict)
