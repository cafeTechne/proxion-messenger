"""R96: unit tests for the reproducibility harness's pure comparison logic.

Only `compare_files` / `format_report` are exercised here (no real PyInstaller
build), loaded from the repo-root scripts/ module by path.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

_HARNESS = (Path(__file__).resolve().parents[2] / "scripts" / "check_reproducible.py")


def _load():
    spec = importlib.util.spec_from_file_location("check_reproducible", _HARNESS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cr = _load()


def test_identical_files_match(tmp_path):
    a = tmp_path / "a"; b = tmp_path / "b"
    a.write_bytes(b"proxion" * 1000)
    b.write_bytes(b"proxion" * 1000)
    rep = cr.compare_files(a, b)
    assert rep["match"] is True
    assert rep["first_diff_offset"] is None
    assert rep["differing_bytes"] == 0
    assert rep["sha_a"] == rep["sha_b"] == hashlib.sha256(b"proxion" * 1000).hexdigest()
    assert "REPRODUCIBLE" in cr.format_report(rep)


def test_same_size_one_byte_differs(tmp_path):
    a = tmp_path / "a"; b = tmp_path / "b"
    a.write_bytes(b"AAAAAAAA")
    b.write_bytes(b"AAAAAaAA")   # differ at offset 5
    rep = cr.compare_files(a, b)
    assert rep["match"] is False
    assert rep["first_diff_offset"] == 5
    assert rep["differing_bytes"] == 1
    assert rep["size_delta"] == 0
    assert "NOT bit-for-bit" in cr.format_report(rep)


def test_different_size_counts_tail_as_differing(tmp_path):
    a = tmp_path / "a"; b = tmp_path / "b"
    a.write_bytes(b"AAAA")
    b.write_bytes(b"AAAABBB")    # 3 extra trailing bytes, overlap identical
    rep = cr.compare_files(a, b)
    assert rep["match"] is False
    assert rep["size_delta"] == 3
    assert rep["first_diff_offset"] == 4   # first diff is at the start of the tail
    assert rep["differing_bytes"] == 3


def test_empty_files_match(tmp_path):
    a = tmp_path / "a"; b = tmp_path / "b"
    a.write_bytes(b"")
    b.write_bytes(b"")
    rep = cr.compare_files(a, b)
    assert rep["match"] is True
    assert rep["differing_bytes"] == 0
