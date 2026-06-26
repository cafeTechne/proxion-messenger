"""Tests: Tauri v1 updater manifest (latest.json) shape validation (R37).

Pure validation of the manifest structure the release pipeline must produce.
No network or build involved — guards against shipping a malformed manifest.
"""
from __future__ import annotations
import pytest


def validate_updater_manifest(manifest: dict) -> list[str]:
    """Return a list of problems with a Tauri v1 latest.json dict. Empty = valid."""
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest is not an object"]
    if not manifest.get("version"):
        problems.append("missing version")
    platforms = manifest.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        problems.append("missing or empty platforms")
        return problems
    for name, entry in platforms.items():
        if not isinstance(entry, dict):
            problems.append(f"{name}: entry is not an object")
            continue
        if not entry.get("url"):
            problems.append(f"{name}: missing url")
        elif not str(entry["url"]).startswith("https://"):
            problems.append(f"{name}: url must be https")
        if not entry.get("signature"):
            problems.append(f"{name}: missing signature")
    return problems


def _good_manifest() -> dict:
    return {
        "version": "v1.2.3",
        "notes": "Bug fixes",
        "pub_date": "2026-06-08T00:00:00Z",
        "platforms": {
            "windows-x86_64": {"signature": "sig1", "url": "https://example.com/p.msi.zip"},
            "darwin-x86_64": {"signature": "sig2", "url": "https://example.com/p.app.tar.gz"},
            "linux-x86_64": {"signature": "sig3", "url": "https://example.com/p.AppImage.tar.gz"},
        },
    }


def test_valid_manifest_passes():
    assert validate_updater_manifest(_good_manifest()) == []


def test_missing_version_flagged():
    m = _good_manifest(); del m["version"]
    assert "missing version" in validate_updater_manifest(m)


def test_missing_signature_flagged():
    m = _good_manifest(); del m["platforms"]["windows-x86_64"]["signature"]
    probs = validate_updater_manifest(m)
    assert any("missing signature" in p for p in probs)


def test_non_https_url_flagged():
    m = _good_manifest(); m["platforms"]["linux-x86_64"]["url"] = "http://insecure/x"
    probs = validate_updater_manifest(m)
    assert any("https" in p for p in probs)
