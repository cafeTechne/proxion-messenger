#!/usr/bin/env python3
"""Measure how reproducible the PyInstaller sidecar build is (E4).

Builds the gateway sidecar twice on this machine and compares the two binaries
byte-for-byte, printing a determinism report. This turns "is it reproducible?"
from a claim into a number we can watch over time and gate on later.

    python scripts/check_reproducible.py            # build twice and compare
    python scripts/check_reproducible.py --compare A B   # just compare two files

Exit codes:
    0  the two builds are byte-identical (fully reproducible)
    2  they differ (report shows how much) -- informational, so CI can run this
       non-blocking until the sidecar is reliably identical
    1  a build failed / usage error

The comparison logic (`compare_files`) is pure and unit-tested; the build
orchestration is a thin wrapper around build_sidecar.py so both use the same
determinism knobs (SOURCE_DATE_EPOCH, PYTHONHASHSEED).
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compare_files(path_a: str | Path, path_b: str | Path) -> dict:
    """Compare two files and return a structured determinism report.

    Pure and side-effect-free (reads the files, nothing else) so it can be unit
    tested without running a real build.

    Keys: match, size_a, size_b, sha_a, sha_b, first_diff_offset (or None),
    differing_bytes (over the overlapping length), size_delta.
    """
    a, b = Path(path_a), Path(path_b)
    data_a = a.read_bytes()
    data_b = b.read_bytes()
    sha_a, sha_b = hashlib.sha256(data_a).hexdigest(), hashlib.sha256(data_b).hexdigest()
    match = data_a == data_b

    first_diff = None
    differing = 0
    overlap = min(len(data_a), len(data_b))
    for i in range(overlap):
        if data_a[i] != data_b[i]:
            if first_diff is None:
                first_diff = i
            differing += 1
    # Bytes past the overlap (a size difference) also count as differing.
    differing += abs(len(data_a) - len(data_b))
    if first_diff is None and len(data_a) != len(data_b):
        first_diff = overlap

    return {
        "match": match,
        "size_a": len(data_a),
        "size_b": len(data_b),
        "size_delta": len(data_b) - len(data_a),
        "sha_a": sha_a,
        "sha_b": sha_b,
        "first_diff_offset": first_diff,
        "differing_bytes": differing,
    }


def format_report(rep: dict) -> str:
    lines = []
    if rep["match"]:
        lines.append("REPRODUCIBLE: the two builds are byte-identical.")
        lines.append(f"  sha256: {rep['sha_a']}")
        lines.append(f"  size:   {rep['size_a']} bytes")
        return "\n".join(lines)
    lines.append("NOT bit-for-bit reproducible yet:")
    lines.append(f"  build A: {rep['sha_a']}  ({rep['size_a']} bytes)")
    lines.append(f"  build B: {rep['sha_b']}  ({rep['size_b']} bytes)")
    lines.append(f"  size delta:        {rep['size_delta']} bytes")
    lines.append(f"  first differing offset: {rep['first_diff_offset']}")
    pct = (100.0 * rep["differing_bytes"] / max(rep["size_a"], rep["size_b"], 1))
    lines.append(f"  differing bytes:   {rep['differing_bytes']} ({pct:.3f}% of the binary)")
    return "\n".join(lines)


def _build_once(dest: Path) -> Path:
    """Run build_sidecar.build() and return the produced sidecar, copied to dest."""
    sys.path.insert(0, str(ROOT))
    import build_sidecar  # noqa: E402  (ROOT is on sys.path)

    build_sidecar.build()
    triple = build_sidecar.get_triple()
    suffix = ".exe" if triple.endswith("windows-msvc") else ""
    produced = build_sidecar.SIDECAR_DIR / f"proxion-gateway-{triple}{suffix}"
    if not produced.exists():
        raise SystemExit(f"build did not produce {produced}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(produced, dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measure sidecar build reproducibility.")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"),
                    help="compare two existing files instead of building")
    args = ap.parse_args(argv)

    if args.compare:
        rep = compare_files(*args.compare)
        print(format_report(rep))
        return 0 if rep["match"] else 2

    tmp = ROOT / "build" / "reproducible"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    print("== build A ==")
    a = _build_once(tmp / "a.bin")
    print("\n== build B ==")
    b = _build_once(tmp / "b.bin")
    print("\n== report ==")
    rep = compare_files(a, b)
    print(format_report(rep))
    return 0 if rep["match"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
