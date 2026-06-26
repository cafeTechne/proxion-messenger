"""R16: Continuous assurance orchestrator.

Runs scheduled evaluations of exit gates, integrity checks, provenance, and
replay-cache health. Computes a consolidated assurance_state (green|amber|red)
and emits signed assurance snapshots.

Set PROXION_ENABLE_CONTINUOUS_ASSURANCE=1 to activate.
Set PROXION_ASSURANCE_INTERVAL_S=300 (default) to control evaluation cadence.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .local_store import LocalStore

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_S = 300
_MAX_CONSECUTIVE_FAILURES = 3

ASSURANCE_GREEN = "green"
ASSURANCE_AMBER = "amber"
ASSURANCE_RED = "red"


def is_continuous_assurance_enabled() -> bool:
    return os.environ.get("PROXION_ENABLE_CONTINUOUS_ASSURANCE") == "1"


def get_assurance_interval() -> int:
    try:
        return int(os.environ.get("PROXION_ASSURANCE_INTERVAL_S", str(_DEFAULT_INTERVAL_S)))
    except (ValueError, TypeError):
        return _DEFAULT_INTERVAL_S


def run_assurance_evaluation(store: Optional["LocalStore"] = None) -> dict:
    """Run a single assurance evaluation cycle.

    Returns dict with: assurance_state, gates, checks, evaluated_at.
    """
    from .security_exit_gates import evaluate_all_gates
    from .provenance_verify import verify_provenance
    from .integrity_consensus import is_consensus_enabled

    results: dict = {
        "id": str(uuid.uuid4()),
        "evaluated_at": time.time(),
        "checks": {},
    }

    gate_summary = evaluate_all_gates(store)
    results["gates"] = gate_summary
    gates_pass = gate_summary.get("all_pass", True)

    try:
        prov = verify_provenance()
        results["checks"]["provenance"] = prov["ok"]
    except Exception:
        results["checks"]["provenance"] = None

    results["checks"]["replay_cache_healthy"] = _check_replay_cache_health(store)
    results["checks"]["consensus_enabled"] = is_consensus_enabled()

    state = _compute_assurance_state(gates_pass, results["checks"])
    results["assurance_state"] = state

    results["signature"] = _sign_assurance_result(results)
    return results


def _compute_assurance_state(gates_pass: bool, checks: dict) -> str:
    provenance_ok = checks.get("provenance")
    replay_ok = checks.get("replay_cache_healthy", True)

    if not gates_pass:
        return ASSURANCE_RED
    if provenance_ok is False or not replay_ok:
        return ASSURANCE_AMBER
    return ASSURANCE_GREEN


def _check_replay_cache_health(store: Optional["LocalStore"]) -> bool:
    if store is None:
        return True
    try:
        import sqlite3
        conn = sqlite3.connect(store.db_path)
        count = conn.execute("SELECT COUNT(*) FROM relay_seen_nonces").fetchone()[0]
        conn.close()
        return count < 100_000
    except Exception:
        return True


def _sign_assurance_result(result: dict) -> str:
    payload = {k: v for k, v in result.items() if k != "signature"}
    canonical = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(canonical).hexdigest()


class ContinuousAssuranceLoop:
    """Manages the continuous assurance background loop."""

    def __init__(self, store: Optional["LocalStore"] = None):
        self._store = store
        self._consecutive_failures = 0
        self._last_state: Optional[str] = None
        self._degraded = False

    def is_degraded(self) -> bool:
        return self._degraded

    def last_state(self) -> Optional[str]:
        return self._last_state

    def run_once(self) -> dict:
        """Run one evaluation cycle. Returns result dict."""
        try:
            result = run_assurance_evaluation(self._store)
            self._consecutive_failures = 0
            self._last_state = result["assurance_state"]
            if self._degraded and result["assurance_state"] == ASSURANCE_GREEN:
                self._degraded = False
                logger.info("Continuous assurance loop recovered to green")
            return result
        except Exception as exc:
            self._consecutive_failures += 1
            logger.error("Assurance evaluation failed (%d): %s", self._consecutive_failures, exc)
            if self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                self._degraded = True
                if self._store:
                    try:
                        self._store.save_security_event(
                            "assurance_loop_degraded", "critical",
                            details=f"consecutive_failures={self._consecutive_failures}",
                        )
                    except Exception:
                        pass
            raise
