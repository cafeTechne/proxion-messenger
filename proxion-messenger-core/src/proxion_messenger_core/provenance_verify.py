"""R15: Runtime build provenance verifier.

Reads artifacts/provenance.json + artifacts/provenance.sig and verifies:
  - required fields are present
  - file hashes match current on-disk files
  - signature is valid (hash-based stub; real deployments use asymmetric keys)

Set PROXION_REQUIRE_BUILD_PROVENANCE=1 to fail hard on missing/invalid provenance.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROVENANCE_INVALID = "build_provenance_invalid"
_REQUIRED_FIELDS = {"commit", "built_at", "files", "toolchain"}


def verify_provenance(provenance_dir: Optional[Path] = None) -> dict:
    """Verify build provenance artifacts.

    Returns dict with: ok (bool), error_code (str), detail (str), fields (dict).
    """
    if provenance_dir is None:
        provenance_dir = _locate_artifacts_dir()

    provenance_path = provenance_dir / "provenance.json"
    sig_path = provenance_dir / "provenance.sig"

    if not provenance_path.exists():
        return {
            "ok": False,
            "error_code": PROVENANCE_INVALID,
            "detail": f"provenance.json not found at {provenance_path}",
            "fields": {},
        }

    try:
        raw = provenance_path.read_bytes()
        manifest = json.loads(raw)
    except Exception as exc:
        return {
            "ok": False,
            "error_code": PROVENANCE_INVALID,
            "detail": f"cannot parse provenance.json: {exc}",
            "fields": {},
        }

    missing = _REQUIRED_FIELDS - set(manifest.keys())
    if missing:
        return {
            "ok": False,
            "error_code": PROVENANCE_INVALID,
            "detail": f"missing required fields: {sorted(missing)}",
            "fields": manifest,
        }

    if sig_path.exists():
        expected_sig = sig_path.read_text().strip()
        actual_sig = hashlib.sha256(raw).hexdigest()
        if expected_sig != actual_sig:
            return {
                "ok": False,
                "error_code": PROVENANCE_INVALID,
                "detail": "provenance.sig does not match provenance.json hash",
                "fields": manifest,
            }

    file_mismatches = _check_file_hashes(manifest.get("files", {}), provenance_dir)
    if file_mismatches:
        return {
            "ok": False,
            "error_code": PROVENANCE_INVALID,
            "detail": f"file hash mismatches: {file_mismatches[:3]}",
            "fields": manifest,
        }

    return {"ok": True, "error_code": "", "detail": "", "fields": manifest}


def enforce_provenance_guard(provenance_dir: Optional[Path] = None) -> None:
    """Raise RuntimeError if build provenance is required but invalid."""
    required = os.environ.get("PROXION_REQUIRE_BUILD_PROVENANCE") == "1"
    if not required:
        return
    result = verify_provenance(provenance_dir)
    if not result["ok"]:
        raise RuntimeError(f"{PROVENANCE_INVALID}: {result['detail']}")


def _check_file_hashes(files: dict, base_dir: Path) -> list:
    mismatches = []
    repo_root = _locate_repo_root(base_dir)
    for rel_path, expected_hash in files.items():
        try:
            candidate = repo_root / rel_path
            if not candidate.exists():
                mismatches.append(f"{rel_path}: not found")
                continue
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if actual != expected_hash:
                mismatches.append(f"{rel_path}: hash mismatch")
        except Exception as exc:
            mismatches.append(f"{rel_path}: error {exc}")
    return mismatches


def _locate_artifacts_dir() -> Path:
    here = Path(__file__).parent
    for _ in range(6):
        candidate = here / "artifacts"
        if candidate.is_dir():
            return candidate
        here = here.parent
    return Path("artifacts")


def _locate_repo_root(artifacts_dir: Path) -> Path:
    return artifacts_dir.parent
