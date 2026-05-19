"""R15: Explicit policy tier state machine with cooldown and audit trail.

Legal transitions:
  normal(0) → elevated(1) → restrictive(2) → containment(3)  (always allowed upward)
  containment(3) → restrictive(2) → elevated(1) → normal(0)  (descent requires cooldown)

De-escalation cooldown: PROXION_POLICY_COOLDOWN_S (default 300 seconds).
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

TIER_NORMAL = 0
TIER_ELEVATED = 1
TIER_RESTRICTIVE = 2
TIER_CONTAINMENT = 3

_TIER_NAMES = {0: "normal", 1: "elevated", 2: "restrictive", 3: "containment"}

_DEFAULT_COOLDOWN_S = 300


class IllegalTierTransition(ValueError):
    """Raised when a tier transition is not permitted."""


class PolicyStateMachine:
    """Manages security tier transitions with cooldown and transition ledger."""

    def __init__(self, cooldown_s: Optional[float] = None):
        self._tier: int = TIER_NORMAL
        self._last_escalation_at: float = 0.0
        self._cooldown_s: float = cooldown_s if cooldown_s is not None else _DEFAULT_COOLDOWN_S
        self._transitions: list[dict] = []

    def current_tier(self) -> int:
        return self._tier

    def transition(
        self,
        target_tier: int,
        trigger_type: str = "auto",
        trigger_detail: str = "",
        actor_webid: str = "",
    ) -> dict:
        """Attempt a tier transition. Returns the transition record or raises IllegalTierTransition."""
        if target_tier < TIER_NORMAL or target_tier > TIER_CONTAINMENT:
            raise IllegalTierTransition(f"invalid tier {target_tier}")

        from_tier = self._tier
        now = time.time()

        if target_tier < from_tier:
            elapsed = now - self._last_escalation_at
            if elapsed < self._cooldown_s:
                remaining = self._cooldown_s - elapsed
                raise IllegalTierTransition(
                    f"cooldown active: {remaining:.0f}s remaining before de-escalation allowed"
                )

        record = {
            "id": str(uuid.uuid4()),
            "from_tier": _TIER_NAMES.get(from_tier, str(from_tier)),
            "to_tier": _TIER_NAMES.get(target_tier, str(target_tier)),
            "trigger_type": trigger_type,
            "trigger_detail": trigger_detail,
            "actor_webid": actor_webid,
            "created_at": now,
        }
        self._tier = target_tier
        if target_tier > from_tier:
            self._last_escalation_at = now
        self._transitions.append(record)
        logger.info(
            "Policy tier %s→%s (trigger=%s %s actor=%s)",
            _TIER_NAMES.get(from_tier), _TIER_NAMES.get(target_tier),
            trigger_type, trigger_detail, actor_webid or "system",
        )
        return record

    def recent_transitions(self, limit: int = 10) -> list[dict]:
        return list(self._transitions[-limit:])

    def can_deescalate(self) -> bool:
        elapsed = time.time() - self._last_escalation_at
        return elapsed >= self._cooldown_s
