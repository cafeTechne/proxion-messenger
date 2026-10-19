"""Every manifest that carries a version agrees with version.txt, the one the
release workflow tags and the gateway reports."""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VERSION = (REPO / "version.txt").read_text(encoding="utf-8").strip()


def _toml_version(path: Path) -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M)
    assert m, f"no version in {path}"
    return m.group(1)


def test_python_package_version():
    assert _toml_version(REPO / "proxion-messenger-core" / "pyproject.toml") == VERSION


def test_cargo_version_and_lock():
    tauri = REPO / "tauri-app" / "src-tauri"
    assert _toml_version(tauri / "Cargo.toml") == VERSION
    lock = (tauri / "Cargo.lock").read_text(encoding="utf-8")
    m = re.search(r'name = "proxion-app"\r?\nversion = "([^"]+)"', lock)
    assert m and m.group(1) == VERSION


def test_tauri_json_versions():
    tauri = REPO / "tauri-app"
    conf = json.loads((tauri / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    assert conf["package"]["version"] == VERSION
    assert json.loads((tauri / "package.json").read_text(encoding="utf-8"))["version"] == VERSION
    lock = json.loads((tauri / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["version"] == VERSION
    assert lock["packages"][""]["version"] == VERSION
