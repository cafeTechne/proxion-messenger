"""Tests for build provenance verification (R15)."""
import json
import os
import tempfile
from pathlib import Path
import pytest

from proxion_messenger_core.provenance_verify import (
    verify_provenance,
    enforce_provenance_guard,
    PROVENANCE_INVALID,
)


@pytest.fixture
def provenance_dir(tmp_path):
    return tmp_path


def _write_valid_provenance(provenance_dir: Path) -> dict:
    import hashlib
    manifest = {
        "commit": "abc123",
        "built_at": "2026-01-01T00:00:00Z",
        "toolchain": {"node": "v22.0.0"},
        "files": {},
    }
    raw_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    (provenance_dir / "provenance.json").write_bytes(raw_bytes)
    sig = hashlib.sha256(raw_bytes).hexdigest()
    (provenance_dir / "provenance.sig").write_text(sig)
    return manifest


class TestStartupFailsWithInvalidProvenance:
    def test_startup_fails_when_provenance_required_and_signature_invalid(self, provenance_dir):
        manifest = {
            "commit": "abc123",
            "built_at": "2026-01-01T00:00:00Z",
            "toolchain": {},
            "files": {},
        }
        (provenance_dir / "provenance.json").write_text(json.dumps(manifest))
        (provenance_dir / "provenance.sig").write_text("wrong_signature")
        result = verify_provenance(provenance_dir)
        assert result["ok"] is False
        assert result["error_code"] == PROVENANCE_INVALID
        assert "sig" in result["detail"].lower() or "hash" in result["detail"].lower()

    def test_startup_fails_when_provenance_required_and_file_missing(self, provenance_dir):
        result = verify_provenance(provenance_dir)
        assert result["ok"] is False
        assert result["error_code"] == PROVENANCE_INVALID


class TestStartupSucceedsWithValidProvenance:
    def test_startup_succeeds_with_valid_provenance_artifacts(self, provenance_dir):
        _write_valid_provenance(provenance_dir)
        result = verify_provenance(provenance_dir)
        assert result["ok"] is True
        assert result["error_code"] == ""

    def test_enforce_guard_noop_when_not_required(self, provenance_dir, monkeypatch):
        monkeypatch.delenv("PROXION_REQUIRE_BUILD_PROVENANCE", raising=False)
        enforce_provenance_guard(provenance_dir)


class TestProvenanceHashMismatch:
    def test_provenance_hash_mismatch_detected(self, provenance_dir):
        manifest = {
            "commit": "abc123",
            "built_at": "2026-01-01T00:00:00Z",
            "toolchain": {},
            "files": {},
        }
        raw = json.dumps(manifest)
        (provenance_dir / "provenance.json").write_text(raw)
        (provenance_dir / "provenance.sig").write_text("0000000000000000000000000000000000000000000000000000000000000000")
        result = verify_provenance(provenance_dir)
        assert result["ok"] is False
        assert result["error_code"] == PROVENANCE_INVALID

    def test_required_fields_must_all_be_present(self, provenance_dir):
        manifest = {"commit": "abc"}
        raw = json.dumps(manifest)
        (provenance_dir / "provenance.json").write_text(raw)
        result = verify_provenance(provenance_dir)
        assert result["ok"] is False
        assert "missing" in result["detail"].lower()
