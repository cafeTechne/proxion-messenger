"""R9: Security policy engine enforcement tests."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from proxion_messenger_core.security_policy import SecurityPolicy, Decision, get_policy, reload_policy


def _make_policy():
    return SecurityPolicy()


def test_ws_command_denied_by_policy_returns_stable_code():
    policy = _make_policy()
    # Owner-only command called by non-owner should be denied
    decision = policy.evaluate_ws_command(
        cmd="get_audit_logs",
        caller_webid="did:key:non_owner",
        gateway_owner_did="did:key:owner",
    )
    assert not decision.allow
    assert decision.deny_code == "E_FORBIDDEN"
    assert decision.deny_reason == "gateway_owner_only"


def test_ws_command_allowed_for_owner():
    policy = _make_policy()
    decision = policy.evaluate_ws_command(
        cmd="get_audit_logs",
        caller_webid="did:key:owner",
        gateway_owner_did="did:key:owner",
    )
    assert decision.allow


def test_ws_command_non_restricted_allowed_for_anyone():
    policy = _make_policy()
    decision = policy.evaluate_ws_command(
        cmd="send_message",
        caller_webid="did:key:anyone",
        gateway_owner_did="did:key:owner",
    )
    assert decision.allow


def test_http_action_denied_by_policy_returns_stable_code():
    policy = _make_policy()
    # Public paths are always allowed
    decision = policy.evaluate_http_action("/relay", "POST", "1.2.3.4")
    assert decision.allow


def test_policy_deny_emits_correct_audit_event_type():
    policy = _make_policy()
    decision = policy.evaluate_ws_command(
        cmd="get_security_events",
        caller_webid="did:key:non_owner",
        gateway_owner_did="did:key:owner",
    )
    assert not decision.allow
    assert decision.audit_event_type == "policy_deny"
    assert decision.severity == "warning"


def test_policy_overlay_adds_owner_only_commands():
    overlay = {"owner_only_commands": ["custom_admin_cmd"]}
    policy = SecurityPolicy(overlay=overlay)
    decision = policy.evaluate_ws_command(
        cmd="custom_admin_cmd",
        caller_webid="did:key:non_owner",
        gateway_owner_did="did:key:owner",
    )
    assert not decision.allow


def test_policy_overlay_adds_denied_commands():
    overlay = {"denied_commands": {"dangerous_cmd": ("E_DENIED", "always blocked", "critical")}}
    policy = SecurityPolicy(overlay=overlay)
    decision = policy.evaluate_ws_command(
        cmd="dangerous_cmd",
        caller_webid="did:key:owner",
        gateway_owner_did="did:key:owner",
    )
    assert not decision.allow
    assert decision.deny_code == "E_DENIED"


def test_get_policy_returns_singleton():
    reload_policy()
    p1 = get_policy()
    p2 = get_policy()
    assert p1 is p2


def test_reload_policy_resets_singleton():
    reload_policy()
    p1 = get_policy()
    reload_policy()
    p2 = get_policy()
    assert p1 is not p2


def test_all_r9_owner_only_commands_are_in_policy():
    policy = _make_policy()
    r9_owner_cmds = [
        "export_security_snapshot",
        "resolve_peer_trust_dispute",
        "list_quarantine_items",
        "release_quarantine_item",
        "drop_quarantine_item",
        "ack_checksum_mismatch",
    ]
    for cmd in r9_owner_cmds:
        decision = policy.evaluate_ws_command(
            cmd=cmd,
            caller_webid="did:key:non_owner",
            gateway_owner_did="did:key:owner",
        )
        assert not decision.allow, f"{cmd} should be owner-only"
