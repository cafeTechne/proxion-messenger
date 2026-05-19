"""Solid SDK migration tracking — shared metrics store.

Collects counts of errors and fallbacks during the legacy→Inrupt SDK
migration.  Import the module-level singleton ``migration_store`` to
record events; the gateway's ``get_solid_migration_errors`` command
reads from it.

Thread safety: all mutations go through ``_lock`` (threading.Lock) so
the store is safe to use from both the asyncio event loop and executor
threads.
"""
from __future__ import annotations

import os
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Normalised error codes (stable across SDK versions)
# ---------------------------------------------------------------------------

SOLID_AUTH_REQUIRED = "SOLID_AUTH_REQUIRED"
SOLID_AUTH_FAILED = "SOLID_AUTH_FAILED"
SOLID_FORBIDDEN = "SOLID_FORBIDDEN"
SOLID_NOT_FOUND = "SOLID_NOT_FOUND"
SOLID_CONFLICT = "SOLID_CONFLICT"
SOLID_PRECONDITION_FAILED = "SOLID_PRECONDITION_FAILED"
SOLID_NETWORK_UNAVAILABLE = "SOLID_NETWORK_UNAVAILABLE"
SOLID_NOT_SUPPORTED = "SOLID_NOT_SUPPORTED"

_ALL_CODES = frozenset({
    SOLID_AUTH_REQUIRED, SOLID_AUTH_FAILED, SOLID_FORBIDDEN, SOLID_NOT_FOUND,
    SOLID_CONFLICT, SOLID_PRECONDITION_FAILED, SOLID_NETWORK_UNAVAILABLE,
    SOLID_NOT_SUPPORTED,
})

# ---------------------------------------------------------------------------
# HTTP status → normalised code mapping
# ---------------------------------------------------------------------------

_HTTP_TO_CODE: Dict[int, str] = {
    401: SOLID_AUTH_REQUIRED,
    403: SOLID_FORBIDDEN,
    404: SOLID_NOT_FOUND,
    409: SOLID_CONFLICT,
    412: SOLID_PRECONDITION_FAILED,
}


def http_status_to_code(status: int) -> str:
    """Return a normalised code for an HTTP status code."""
    return _HTTP_TO_CODE.get(status, SOLID_AUTH_FAILED)


# ---------------------------------------------------------------------------
# Migration modes
# ---------------------------------------------------------------------------

MODE_LEGACY = "legacy"
MODE_BRIDGE = "inrupt_bridge"
MODE_AUTO = "auto"
MODE_SDK = "sdk"
_VALID_MODES = frozenset({MODE_LEGACY, MODE_BRIDGE, MODE_AUTO, MODE_SDK})


def current_auth_mode() -> str:
    return os.environ.get("PROXION_SOLID_AUTH_MODE", MODE_LEGACY)


def current_notifs_mode() -> str:
    return os.environ.get("PROXION_SOLID_NOTIFS_MODE", MODE_AUTO)


def current_cutover_stage() -> int:
    try:
        return int(os.environ.get("PROXION_SOLID_CUTOVER_STAGE", "0"))
    except (ValueError, TypeError):
        return 0


def access_grants_enabled() -> bool:
    return os.environ.get("PROXION_ENABLE_ACCESS_GRANTS", "0") == "1"


# ---------------------------------------------------------------------------
# MigrationErrorStore
# ---------------------------------------------------------------------------

@dataclass
class MigrationErrorStore:
    """Accumulates error and fallback events for the migration dashboard."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    # code -> mode -> count
    _counts: Dict[str, Dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    _fallback_count: int = 0
    _last_fallback_reason: Optional[str] = None
    _auth_mode_active: str = MODE_LEGACY
    _auth_mode_fallback_count: int = 0
    _auth_mode_last_failure_code: Optional[str] = None
    _notifs_fallback_count: int = 0
    _notifs_last_fallback_reason: Optional[str] = None
    _dual_read_mismatch_count: int = 0

    def record(self, code: str, mode: str = MODE_LEGACY) -> None:
        """Increment the counter for *code* in *mode*."""
        with self._lock:
            self._counts[code][mode] += 1

    def record_auth_fallback(self, failure_code: str) -> None:
        with self._lock:
            self._auth_mode_fallback_count += 1
            self._auth_mode_last_failure_code = failure_code

    def record_notifs_fallback(self, reason: str) -> None:
        with self._lock:
            self._notifs_fallback_count += 1
            self._notifs_last_fallback_reason = reason

    def record_dual_read_mismatch(self) -> None:
        with self._lock:
            self._dual_read_mismatch_count += 1

    def set_auth_mode(self, mode: str) -> None:
        with self._lock:
            self._auth_mode_active = mode

    def snapshot(self) -> dict:
        """Return a JSON-serialisable snapshot of all metrics."""
        with self._lock:
            by_code = {
                code: dict(modes)
                for code, modes in self._counts.items()
            }
            return {
                "by_code": by_code,
                "auth_mode_active": self._auth_mode_active,
                "auth_mode_fallback_count": self._auth_mode_fallback_count,
                "auth_mode_last_failure_code": self._auth_mode_last_failure_code,
                "notifs_fallback_count": self._notifs_fallback_count,
                "notifs_last_fallback_reason": self._notifs_last_fallback_reason,
                "dual_read_mismatch_count": self._dual_read_mismatch_count,
                "cutover_stage": current_cutover_stage(),
            }


# Module-level singleton used everywhere
migration_store = MigrationErrorStore()
