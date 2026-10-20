"""Every event type the gateway sends is either known to the web client or
listed here as not meant for it.

The web client's dispatch (web/main.js and its modules) cannot run under
pytest, so this is a ratchet rather than a behaviour test: a new gateway event
the browser never looks at would otherwise be dropped silently. When this
fails, handle the event in the web client or, if only the CLI, MCP or admin
tools consume it, add it below. Remove names from the list as the web client
starts handling them.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "proxion-messenger-core" / "src" / "proxion_messenger_core"
WEB = ROOT / "web"

# Sent by the gateway, never referenced by the web client. Mostly responses to
# admin, security and recovery commands the browser does not send, and the
# sender-key / session protocol the web client does not implement.
NOT_FOR_WEB = {
    "access_grants_policy_state", "ack", "ack_delivered_ok", "ack_read_ok",
    "admin_action_confirmed", "admin_action_requested", "attachment_key", "audit_logs",
    "catch_up_ack_ok", "catch_up_batch", "checksum_mismatch_acked",
    "compromise_recovery_aborted", "compromise_recovery_resumed",
    "compromise_recovery_started", "compromise_recovery_status", "connect_id",
    "connect_id_resolved", "contact_verification", "contact_verification_sync_ack",
    "contact_verifications_sync", "contact_verified", "degraded_mode_state",
    "device_recovery_code_accepted", "device_recovery_code_generated",
    "device_recovery_code_invalid", "device_registered", "device_unregistered",
    "distribute_sender_key_ack", "dm_session_init_required", "dm_sessions", "file_progress",
    "hole_punch_complete_ack", "hole_punch_initiated", "hole_punch_offer",
    "identity_restore", "incident_simulation_report", "invite_accepted", "invite_created",
    "msg_delivered", "msg_read", "new_member_joined", "notification_read", "notifications",
    "outbox_failed", "peer_devices", "peer_gateway_change_approved",
    "peer_trust_dispute_resolved", "pod_linked", "prekey_bundle",
    "prekey_replenishment_needed", "prekeys_uploaded", "primary_device_set",
    "quarantine_item_dropped", "quarantine_item_released", "quarantine_items",
    "realtime_abuse_signals", "receipts", "recovery_drill_report", "recovery_drill_result",
    "recovery_drill_templates", "recovery_operation_confirmed",
    "recovery_operation_prepared", "retention_lock_cleared", "retention_lock_set",
    "retention_locks", "room_key_update", "runtime_security_state", "sealed_dm_sent",
    "sealed_message", "security_event_stream", "security_events",
    "security_exit_gate_status", "security_self_test_report", "security_snapshot",
    "security_summary", "security_tier_set", "security_tier_state", "send_dm_ack",
    "send_room_ack", "sender_key", "sender_key_distributed",
    "sender_key_distribution_needed", "sender_key_received", "sender_key_rotation",
    "sender_key_rotation_acked", "sender_key_uploaded", "seq_num", "session_expired",
    "session_ready_ack", "session_recovery_deferred", "session_recovery_initiated",
    "session_recovery_required", "session_reset_deferred", "session_reset_pending",
    "session_reset_requested", "solid_migration_errors", "spk_rotated",
    "spk_rotation_needed", "trust_revocation_created", "trust_revocations",
    "verified_contacts", "voice_channel_join_relayed", "voice_join", "voice_leave",
    "webhook_rotated",
}

_TYPE = re.compile(r"[\"']type[\"']\s*:\s*[\"']([a-z_][a-z0-9_]*)[\"']")


def _emitted() -> set[str]:
    out: set[str] = set()
    for p in SRC.glob("*.py"):
        out |= set(_TYPE.findall(p.read_text(encoding="utf-8")))
    return out


def _web_source() -> str:
    return "".join(
        p.read_text(encoding="utf-8") for p in WEB.glob("*.js")
        if not p.name.endswith(".test.js") and not p.name.startswith("smoke")
    )


def _known_to_web(event: str, web: str) -> bool:
    return re.search(r"[\"'`]" + re.escape(event) + r"[\"'`]", web) is not None


def test_every_gateway_event_is_handled_or_listed():
    web = _web_source()
    unhandled = sorted(t for t in _emitted() if not _known_to_web(t, web) and t not in NOT_FOR_WEB)
    assert not unhandled, (
        "gateway sends event types the web client never handles: "
        f"{unhandled}. Handle them in web/ or add them to NOT_FOR_WEB."
    )


def test_list_has_no_stale_entries():
    web, emitted = _web_source(), _emitted()
    stale = sorted(t for t in NOT_FOR_WEB if t not in emitted or _known_to_web(t, web))
    assert not stale, f"remove from NOT_FOR_WEB (no longer sent, or now handled): {stale}"
