"""R16: Cryptographic key lifecycle policy evaluator.

Checks identity key age and store key age against configured max ages and
rotation cadence requirements. Escalates security tier when keys are overdue.

Configuration (seconds):
  PROXION_IDENTITY_KEY_MAX_AGE_S   — default 7776000 (90 days)
  PROXION_STORE_KEY_MAX_AGE_S      — default 15552000 (180 days)
  PROXION_KEY_LIFECYCLE_ESCALATION — off|warn|restrictive|containment
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_IDENTITY_MAX_AGE_S = 7_776_000   # 90 days
_DEFAULT_STORE_MAX_AGE_S = 15_552_000     # 180 days
_GRACE_WINDOW_S = 86_400                   # 1 day

KEY_OVERDUE_WARNING = "key_lifecycle_overdue_warning"
KEY_POLICY_VIOLATION = "key_lifecycle_policy_violation"


def evaluate_key_lifecycle(
    identity_key_created_at: Optional[float],
    store_key_created_at: Optional[float],
    identity_key_rotated_at: Optional[float] = None,
    store_key_rotated_at: Optional[float] = None,
) -> dict:
    """Evaluate key ages against lifecycle policy.

    Returns dict with:
      ok (bool), violations (list[str]), warnings (list[str]),
      remediation (list[str]), identity_age_days (float), store_age_days (float)
    """
    now = time.time()
    identity_max = int(os.environ.get("PROXION_IDENTITY_KEY_MAX_AGE_S", str(_DEFAULT_IDENTITY_MAX_AGE_S)))
    store_max = int(os.environ.get("PROXION_STORE_KEY_MAX_AGE_S", str(_DEFAULT_STORE_MAX_AGE_S)))

    violations = []
    warnings = []
    remediation = []

    identity_ref = identity_key_rotated_at or identity_key_created_at or now
    store_ref = store_key_rotated_at or store_key_created_at or now

    identity_age = now - identity_ref
    store_age = now - store_ref

    if identity_age > identity_max + _GRACE_WINDOW_S:
        violations.append(f"identity_key_overdue: age={identity_age:.0f}s max={identity_max}s")
        remediation.append("rotate_identity")
    elif identity_age > identity_max:
        warnings.append(f"identity_key_approaching_max_age: age={identity_age:.0f}s")
        remediation.append("rotate_identity")

    if store_age > store_max + _GRACE_WINDOW_S:
        violations.append(f"store_key_overdue: age={store_age:.0f}s max={store_max}s")
        remediation.append("rotate_store")
    elif store_age > store_max:
        warnings.append(f"store_key_approaching_max_age: age={store_age:.0f}s")
        remediation.append("rotate_store")

    return {
        "ok": len(violations) == 0,
        "violations": violations,
        "warnings": warnings,
        "remediation": list(dict.fromkeys(remediation)),
        "identity_age_days": identity_age / 86400,
        "store_age_days": store_age / 86400,
    }


def apply_key_lifecycle_escalation(result: dict, store=None) -> None:
    """Escalate security tier if key lifecycle violations exist."""
    mode = os.environ.get("PROXION_KEY_LIFECYCLE_ESCALATION", "warn")
    if mode == "off":
        return
    if result["violations"]:
        event_type = KEY_POLICY_VIOLATION
        severity = "critical"
    elif result["warnings"]:
        event_type = KEY_OVERDUE_WARNING
        severity = "warning"
    else:
        return

    if store:
        try:
            detail = "; ".join(result["violations"] + result["warnings"])
            store.save_security_event(event_type, severity, details=detail)
        except Exception:
            pass

    if result["violations"] and mode in ("restrictive", "containment"):
        try:
            from .security_policy import get_policy, TIER_RESTRICTIVE, TIER_CONTAINMENT
            pol = get_policy()
            target = TIER_CONTAINMENT if mode == "containment" else TIER_RESTRICTIVE
            if pol.get_tier() < target:
                pol.set_tier(target, reason="key_lifecycle_violation")
                logger.warning("Security tier escalated to %s due to key lifecycle violation", mode)
        except Exception as exc:
            logger.error("Failed to escalate tier for key lifecycle: %s", exc)
