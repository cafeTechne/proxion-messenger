#!/usr/bin/env python3
"""Cold-path undefined-name gate (ROADMAP_2 H1).

The `_write_json` / `_j` / `pub_key_to_did` bug class — a name referenced but never
defined or imported *in its scope*, reachable only from an error/recovery branch —
is invisible to both the test suite and runtime until that cold path fires. We hit
several of these (3 idempotency-replay acks using a locally-aliased `_j`, the invite
flood-control `pub_key_to_did` call, and a `MagicMock` leak into production
serialization), all found via ruff's pyflakes name analysis rather than tests.

This gate runs that analysis across the backend so any *new* such landmine fails CI:

  F821  undefined name        (the core cold-path class)
  F822  undefined name in __all__

`F811` (redefinition) is intentionally excluded — the codebase has ~33 harmless
redundant local `import os` shadows that aren't this bug class.

The JS half of this net is `cd web && npm run lint` (ESLint `no-undef`), already in CI.

Usage:  python scripts/check_cold_paths.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RULES = "F821,F822"
SRC = Path(__file__).resolve().parent.parent / "proxion-messenger-core" / "src"


def main() -> int:
    if not SRC.is_dir():
        print(f"[cold-path-gate] source dir not found: {SRC}", file=sys.stderr)
        return 2
    print(f"[cold-path-gate] ruff --select {RULES} on {SRC}")
    try:
        proc = subprocess.run(
            ["ruff", "check", "--select", RULES, str(SRC), "--output-format=concise"],
        )
    except FileNotFoundError:
        print("[cold-path-gate] ruff not installed — `pip install ruff`.", file=sys.stderr)
        return 2
    if proc.returncode == 0:
        print("[cold-path-gate] OK - no undefined-name landmines.")
    else:
        print(
            "[cold-path-gate] FAIL - for each name above, define/import it in scope, "
            "or (for annotation-only names under `from __future__ import annotations`) "
            "add a `if TYPE_CHECKING:` import.",
            file=sys.stderr,
        )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
