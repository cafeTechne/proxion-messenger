"""R16: Autonomous policy quality controls.

Detects escalation churn and quality degradation in the adaptive tier system.
When churn exceeds threshold, freezes auto-escalation and requires manual confirmation.

Churn is defined as: tier escalations + de-escalations within a rolling window.
"""
from __future__ import annotations

import logging
import time
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .local_store import LocalStore

logger = logging.getLogger(__name__)

QUALITY_GUARD_EVENT = "policy_quality_guard_triggered"
_DEFAULT_CHURN_THRESHOLD = 10
_DEFAULT_CHURN_WINDOW_S = 3600


class PolicyQualityMonitor:
    """Tracks escalation churn and manages the quality guard."""

    def __init__(
        self,
        churn_threshold: int = _DEFAULT_CHURN_THRESHOLD,
        churn_window_s: float = _DEFAULT_CHURN_WINDOW_S,
    ):
        self._threshold = churn_threshold
        self._window_s = churn_window_s
        self._transition_times: list[float] = []
        self._auto_escalation_frozen: bool = False

    def record_transition(self, from_tier: int, to_tier: int) -> None:
        """Record a tier transition for churn tracking."""
        now = time.time()
        self._transition_times.append(now)
        self._prune_old_transitions(now)

    def churn_count(self) -> int:
        """Return number of transitions in the current window."""
        self._prune_old_transitions(time.time())
        return len(self._transition_times)

    def is_auto_escalation_frozen(self) -> bool:
        return self._auto_escalation_frozen

    def evaluate_quality(self, store: Optional["LocalStore"] = None) -> dict:
        """Check quality and freeze auto-escalation if churn exceeds threshold.

        Returns dict with: ok (bool), churn_count (int), frozen (bool), reason (str).
        """
        churn = self.churn_count()
        if churn >= self._threshold and not self._auto_escalation_frozen:
            self._auto_escalation_frozen = True
            logger.warning(
                "Policy quality guard triggered: churn=%d threshold=%d — auto-escalation frozen",
                churn, self._threshold,
            )
            if store:
                try:
                    store.save_security_event(
                        QUALITY_GUARD_EVENT, "warning",
                        details=f"churn={churn} threshold={self._threshold}",
                    )
                except Exception:
                    pass

        return {
            "ok": not self._auto_escalation_frozen,
            "churn_count": churn,
            "frozen": self._auto_escalation_frozen,
            "threshold": self._threshold,
            "reason": "escalation_churn_excessive" if self._auto_escalation_frozen else "within_threshold",
        }

    def unfreeze(self) -> None:
        """Manually unfreeze auto-escalation (requires operator action)."""
        self._auto_escalation_frozen = False
        self._transition_times.clear()
        logger.info("Policy auto-escalation unfrozen by operator")

    def _prune_old_transitions(self, now: float) -> None:
        cutoff = now - self._window_s
        self._transition_times = [t for t in self._transition_times if t >= cutoff]


_global_monitor: Optional[PolicyQualityMonitor] = None


def get_quality_monitor() -> PolicyQualityMonitor:
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = PolicyQualityMonitor()
    return _global_monitor
