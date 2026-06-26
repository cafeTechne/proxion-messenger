"""Centralized security policy engine (Round 9) with adaptive tiers (Round 10).

evaluate_ws_command  — gates WebSocket commands before handler dispatch.
evaluate_http_action — gates HTTP actions before handler dispatch.

Adaptive tiers (R10):
  T0 — normal
  T1 — elevated: rate limits tightened 2x
  T2 — restrictive: high-risk mutating commands/endpoints blocked
  T3 — containment: federation quarantine forced, restore/import disabled

Policies are loaded from static defaults and optionally overlaid with a JSON
file specified by PROXION_POLICY_FILE.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


class PolicyLoadError(RuntimeError):
    """Raised when policy cannot be loaded due to hash mismatch or other constraint."""

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

TIER_NORMAL = 0
TIER_ELEVATED = 1
TIER_RESTRICTIVE = 2
TIER_CONTAINMENT = 3

# Tier escalation thresholds (rolling signals from get_abuse_signal_rollups(hours=1))
_TIER1_AUTH_LOCKOUTS = 3
_TIER1_SCHEMA_REJECTS = 10
_TIER2_AUTH_LOCKOUTS = 8
_TIER2_REPLAY_REJECTS = 50
_TIER3_AUTH_LOCKOUTS = 15
_TIER3_DB_INTEGRITY = 1

# Commands blocked at Tier 2+
_TIER2_BLOCKED_COMMANDS: frozenset = frozenset({
    "prepare_recovery_operation", "confirm_recovery_operation",
})

# Commands blocked at Tier 3 (containment) — everything in T2 plus these
_TIER3_BLOCKED_COMMANDS: frozenset = frozenset(_TIER2_BLOCKED_COMMANDS | {
    "connect_css", "disconnect_pod", "reconnect_pod",
})

# HTTP paths blocked at Tier 2+
_TIER2_BLOCKED_PATHS: frozenset = frozenset({"/restore", "/import"})

# HTTP paths blocked at Tier 3 (in addition to T2)
_TIER3_BLOCKED_PATHS: frozenset = frozenset(_TIER2_BLOCKED_PATHS | {"/export"})


@dataclass
class Decision:
    allow: bool
    deny_code: str = ""
    deny_reason: str = ""
    severity: str = "info"
    audit_event_type: str = ""
    policy_ref: dict = field(default_factory=dict)


_ALLOW = Decision(allow=True)


# ---------------------------------------------------------------------------
# Static default policy tables
# ---------------------------------------------------------------------------

_DENIED_COMMANDS: dict[str, tuple[str, str, str]] = {}

_OWNER_ONLY_COMMANDS: set[str] = {
    "get_audit_logs", "get_security_events", "connect_css", "disconnect_pod",
    "reconnect_pod", "get_runtime_security_state", "get_security_summary",
    "get_degraded_mode_state", "get_realtime_abuse_signals",
    "approve_peer_gateway_change", "prepare_recovery_operation",
    "confirm_recovery_operation", "export_security_snapshot",
    "resolve_peer_trust_dispute", "list_quarantine_items",
    "release_quarantine_item", "drop_quarantine_item", "ack_checksum_mismatch",
    # R10
    "set_security_tier", "get_security_tier_state",
    "set_retention_lock", "list_retention_locks", "clear_retention_lock",
    "run_security_self_test",
    # R11
    "request_admin_action", "confirm_admin_action",
    "simulate_incident_policy",
    "create_trust_revocation", "list_trust_revocations",
    # R12
    "start_compromise_recovery", "get_compromise_recovery_status",
    "resume_compromise_recovery", "abort_compromise_recovery",
    "get_security_event_stream",
    # R14
    "get_access_grants_policy_state",
    # R15
    "get_security_exit_gate_status",
    # R16
    "run_recovery_drill",
    "list_recovery_drill_templates",
    "get_recovery_drill_report",
}

_RESTRICTED_HTTP_PATHS: set[str] = {
    "/restore", "/import", "/export", "/security-snapshot",
}

_PUBLIC_HTTP_PATHS: set[str] = {
    "/relay", "/invite", "/invite/accept",
    "/.well-known/proxion", "/health",
}


class SecurityPolicy:
    """Loaded once at startup; thread-safe for reads.

    Also carries adaptive tier state (R10) — mutable via set_tier().
    """

    def __init__(
        self,
        overlay: Optional[dict] = None,
        policy_id: Optional[str] = None,
        policy_version: str = "1",
        loaded_from: str = "defaults",
        policy_sha256: str = "",
    ):
        self._denied_commands: dict = dict(_DENIED_COMMANDS)
        self._owner_only: set = set(_OWNER_ONLY_COMMANDS)
        self._restricted_http: set = set(_RESTRICTED_HTTP_PATHS)
        # Adaptive tier state
        self._tier: int = TIER_NORMAL
        self._tier_override_until: float = 0.0
        self._tier_reasons: list = []
        # Provenance metadata (R12)
        self._policy_id: str = policy_id or str(uuid.uuid4())
        self._policy_version: str = policy_version
        self._loaded_from: str = loaded_from
        self._policy_sha256: str = policy_sha256
        if overlay:
            self._apply_overlay(overlay)

    def get_provenance(self) -> dict:
        """Return policy provenance metadata."""
        return {
            "policy_id": self._policy_id,
            "policy_version": self._policy_version,
            "loaded_from": self._loaded_from,
            "sha256": self._policy_sha256,
        }

    def _policy_ref(self) -> dict:
        return self.get_provenance()

    def _apply_overlay(self, overlay: dict) -> None:
        extra_owner_only = overlay.get("owner_only_commands", [])
        if isinstance(extra_owner_only, list):
            self._owner_only.update(extra_owner_only)
        extra_deny = overlay.get("denied_commands", {})
        if isinstance(extra_deny, dict):
            self._denied_commands.update(extra_deny)

    # ------------------------------------------------------------------
    # Tier management (R10)
    # ------------------------------------------------------------------

    def set_tier(self, tier: int, override_ttl_s: Optional[float] = None, reason: str = "") -> None:
        """Set the adaptive security tier. Pass override_ttl_s to auto-expire the override."""
        self._tier = max(TIER_NORMAL, min(TIER_CONTAINMENT, tier))
        if override_ttl_s is not None and override_ttl_s > 0:
            self._tier_override_until = time.time() + override_ttl_s
        else:
            self._tier_override_until = 0.0
        if reason:
            self._tier_reasons = [reason]
        logger.info("Security tier set to T%d (reason=%s, ttl=%s)", self._tier, reason, override_ttl_s)

    def get_tier(self) -> int:
        """Return current tier, expiring any TTL override if elapsed."""
        if self._tier_override_until > 0 and time.time() > self._tier_override_until:
            self._tier = TIER_NORMAL
            self._tier_override_until = 0.0
            self._tier_reasons = []
        return self._tier

    def get_tier_state(self) -> dict:
        tier = self.get_tier()
        return {
            "tier": tier,
            "tier_name": ["normal", "elevated", "restrictive", "containment"][tier],
            "override_until": self._tier_override_until if self._tier_override_until > 0 else None,
            "reasons": list(self._tier_reasons),
        }

    def apply_drift_escalation(self, drift_severity: str) -> int:
        """Escalate security tier based on spec drift severity.

        Reads PROXION_DRIFT_ESCALATION_MODE (off|restrictive|containment).
        High/critical severity triggers escalation to the configured tier.
        Returns current tier after any escalation.
        """
        mode = os.environ.get("PROXION_DRIFT_ESCALATION_MODE", "off")
        if mode == "off":
            return self.get_tier()

        if drift_severity in ("high", "critical"):
            target = TIER_CONTAINMENT if mode == "containment" else TIER_RESTRICTIVE
            if self.get_tier() < target:
                self.set_tier(target, reason=f"spec_drift_{drift_severity}")
                self._drift_protection_active = True

        return self.get_tier()

    def is_drift_protection_active(self) -> bool:
        """Return True when tier was escalated due to spec drift."""
        return getattr(self, "_drift_protection_active", False) and self.get_tier() >= TIER_RESTRICTIVE

    def escalate_tier_from_signals(self, signals: dict) -> int:
        """Evaluate rolling abuse signals and escalate tier if thresholds exceeded.

        signals: output of store.get_abuse_signal_rollups(hours=1)
        Returns new tier.
        """
        auth = signals.get("auth_lockouts", 0)
        schema = signals.get("schema_rejects", 0)
        replay = signals.get("replay_rejects", 0)
        db_int = signals.get("db_integrity_events", 0)
        policy_denies = signals.get("policy_deny_events", 0)

        reasons = []
        new_tier = TIER_NORMAL

        if db_int >= _TIER3_DB_INTEGRITY or auth >= _TIER3_AUTH_LOCKOUTS:
            new_tier = TIER_CONTAINMENT
            reasons.append(f"db_integrity={db_int} auth_lockouts={auth}")
        elif auth >= _TIER2_AUTH_LOCKOUTS or replay >= _TIER2_REPLAY_REJECTS:
            new_tier = TIER_RESTRICTIVE
            reasons.append(f"auth_lockouts={auth} replay_rejects={replay}")
        elif auth >= _TIER1_AUTH_LOCKOUTS or schema >= _TIER1_SCHEMA_REJECTS:
            new_tier = TIER_ELEVATED
            reasons.append(f"auth_lockouts={auth} schema_rejects={schema}")

        if new_tier > self._tier:
            try:
                from .policy_quality import get_quality_monitor
                monitor = get_quality_monitor()
                quality = monitor.evaluate_quality()
                if quality["frozen"]:
                    logger.warning(
                        "Auto-escalation blocked by policy quality guard (churn=%d)",
                        quality["churn_count"],
                    )
                    return self._tier
                monitor.record_transition(self._tier, new_tier)
            except Exception:
                pass
            self._tier = new_tier
            self._tier_reasons = reasons
            logger.warning("Security tier auto-escalated to T%d: %s", new_tier, reasons)

        return self._tier

    # ------------------------------------------------------------------
    # Policy evaluation
    # ------------------------------------------------------------------

    def evaluate_ws_command(
        self,
        cmd: str,
        caller_webid: str,
        gateway_owner_did: str,
        context: Optional[dict] = None,
    ) -> Decision:
        if cmd in self._denied_commands:
            code, reason, sev = self._denied_commands[cmd]
            return Decision(allow=False, deny_code=code, deny_reason=reason,
                            severity=sev, audit_event_type="policy_deny")

        tier = self.get_tier()

        # Tier 3 containment blocks
        if tier >= TIER_CONTAINMENT and cmd in _TIER3_BLOCKED_COMMANDS:
            return Decision(allow=False, deny_code="E_CONTAINMENT",
                            deny_reason="containment_mode_active",
                            severity="warning", audit_event_type="policy_deny")

        # Tier 2 restrictive blocks
        if tier >= TIER_RESTRICTIVE and cmd in _TIER2_BLOCKED_COMMANDS:
            return Decision(allow=False, deny_code="E_RESTRICTED",
                            deny_reason="restrictive_mode_active",
                            severity="warning", audit_event_type="policy_deny")

        if cmd in self._owner_only:
            if caller_webid != gateway_owner_did:
                d = Decision(allow=False, deny_code="E_FORBIDDEN",
                             deny_reason="gateway_owner_only",
                             severity="warning", audit_event_type="policy_deny")
                d.policy_ref = self._policy_ref()
                return d
        d = Decision(allow=True)
        d.policy_ref = self._policy_ref()
        return d

    def evaluate_http_action(
        self,
        path: str,
        method: str,
        peer_ip: str,
        context: Optional[dict] = None,
    ) -> Decision:
        if path in _PUBLIC_HTTP_PATHS:
            return _ALLOW

        tier = self.get_tier()

        if tier >= TIER_CONTAINMENT and path in _TIER3_BLOCKED_PATHS:
            return Decision(allow=False, deny_code="E_CONTAINMENT",
                            deny_reason="containment_mode_active",
                            severity="warning", audit_event_type="policy_deny")

        if tier >= TIER_RESTRICTIVE and path in _TIER2_BLOCKED_PATHS:
            return Decision(allow=False, deny_code="E_RESTRICTED",
                            deny_reason="restrictive_mode_active",
                            severity="warning", audit_event_type="policy_deny")

        return _ALLOW

    def get_rate_multiplier(self) -> float:
        """Return rate-limit multiplier for current tier. T1+ = 2x tighter (0.5x allowance)."""
        tier = self.get_tier()
        if tier >= TIER_ELEVATED:
            return 0.5  # T1+: allow half as many requests
        return 1.0


_policy: Optional[SecurityPolicy] = None


def get_policy() -> SecurityPolicy:
    """Return the singleton policy, loading from disk if needed."""
    global _policy
    if _policy is None:
        _policy = _load_policy()
    return _policy


def _load_policy() -> SecurityPolicy:
    policy_path = os.environ.get("PROXION_POLICY_FILE", "")
    overlay: Optional[dict] = None
    loaded_from = "defaults"
    policy_sha256 = ""
    if policy_path:
        try:
            with open(policy_path, "rb") as f:
                raw = f.read()
            policy_sha256 = hashlib.sha256(raw).hexdigest()
            overlay = json.loads(raw.decode("utf-8"))
            loaded_from = policy_path
            logger.info("Security policy overlay loaded from %s (sha256=%s)", policy_path, policy_sha256[:16])
        except Exception as exc:
            logger.warning("Failed to load policy overlay from %s: %s", policy_path, exc)

    required_hash = os.environ.get("PROXION_REQUIRE_POLICY_HASH", "")
    if required_hash and policy_sha256 != required_hash:
        raise PolicyLoadError(
            f"policy_hash_mismatch: expected={required_hash[:16]} actual={policy_sha256[:16]}"
        )

    return SecurityPolicy(
        overlay=overlay,
        loaded_from=loaded_from,
        policy_sha256=policy_sha256,
    )


def reload_policy() -> None:
    """Force reload of policy on next access."""
    global _policy
    _policy = None
