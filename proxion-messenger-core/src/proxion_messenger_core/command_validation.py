"""Command payload schema validation for ProxionGateway WebSocket commands."""
from __future__ import annotations

import re as _re
import unicodedata as _unicodedata

_MAX_CONTENT = 16_384   # bytes  (live messages)
_MAX_SCHED   = 4_096    # bytes  (scheduled messages)
_MAX_ID      = 256      # chars  (IDs, room codes, cert IDs)
_MAX_NAME    = 100      # chars  (display names, room names)
_MAX_SDP     = 65_536   # bytes  (WebRTC SDP offers/answers)
_MAX_ICE     = 4_096    # bytes  (ICE candidate strings)
_MAX_SIG     = 512      # bytes  (Ed25519 signatures base64url)
_MAX_EMOJI   = 12       # chars  (emoji — allow multi-codepoint sequences)

_DID_KEY_RE = _re.compile(r"^did:key:z[1-9A-HJ-NP-Za-km-z]+$")
_HTTPS_URL_RE = _re.compile(r"^https?://")
_VALID_DIRECTIONS = frozenset({"incoming", "outgoing"})
_VALID_ROLES = frozenset({"owner", "admin", "mod", "member"})

_TEXT_FIELDS = frozenset({"display_name", "room_name", "name", "status_message", "bot_name", "topic"})
_CONTROL_CHAR_RE = _re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

def normalize_text_field(value: str) -> str:
    """NFC normalize and strip control characters from a user-visible text field."""
    normalized = _unicodedata.normalize("NFC", value)
    return _CONTROL_CHAR_RE.sub("", normalized)


def _is_did_key(v: str) -> bool:
    return bool(_DID_KEY_RE.match(v))


def _is_https_url(v: str) -> bool:
    return bool(_HTTPS_URL_RE.match(v))


def _is_valid_direction(v: str) -> bool:
    return v in _VALID_DIRECTIONS


def _is_valid_role(v: str) -> bool:
    return v in _VALID_ROLES


# Per-command required fields: {field_name: (expected_type, max_len_or_None[, validator_fn])}
# validator_fn(value: str) -> bool — raise SchemaError when it returns False
_SCHEMA: dict[str, dict[str, tuple]] = {
    "send_dm":          {"cert_id": (str, _MAX_ID), "content": (str, _MAX_CONTENT)},
    "send_room":        {"room_id": (str, _MAX_ID), "content": (str, _MAX_CONTENT)},
    "local_dm":         {"target_webid": (str, _MAX_ID), "content": (str, _MAX_CONTENT)},
    "edit_message":     {"message_id": (str, _MAX_ID), "content": (str, _MAX_CONTENT)},
    "edit_local_message": {"message_id": (str, _MAX_ID), "content": (str, _MAX_CONTENT)},
    # register accepts either "did" OR "webid" — handler validates internally
    "auth_response":    {"signature": (str, _MAX_SIG)},
    "join_room":        {"code": (str, _MAX_ID)},
    "join_remote_room": {"url": (str, 2048)},
    # chat_room_create: name is optional and handler truncates at 100 chars
    "chat_room_create": {},
    "set_presence":     {"status": (str, 32)},
    "voice_invite":     {"target_webid": (str, _MAX_ID)},   # sdp_offer optional
    "voice_answer":     {"session_id": (str, _MAX_ID)},     # sdp_answer optional in some flows
    "ice_candidate":    {"session_id": (str, _MAX_ID), "candidate": (str, _MAX_ICE)},
    "voice_hangup":     {"session_id": (str, _MAX_ID)},
    "schedule_message": {
        "thread_id": (str, _MAX_ID),
        "content":   (str, _MAX_SCHED),
        "send_at":   (str, 64),
    },
    "mark_read":        {"thread_id": (str, _MAX_ID)},
    "delete_room":      {"room_id": (str, _MAX_ID)},
    "kick_member":      {"room_id": (str, _MAX_ID), "webid": (str, _MAX_ID)},
    # pin/unpin use thread_id (may be prefixed "room:…") — validated by handler
    "pin_message":      {"message_id": (str, _MAX_ID)},
    "unpin_message":    {"message_id": (str, _MAX_ID)},
    "block":            {"webid": (str, _MAX_ID)},
    "unblock":          {"webid": (str, _MAX_ID)},
    # typing uses room_id or cert_id — no single required field; validated by handler
    "add_reaction":     {"message_id": (str, _MAX_ID), "emoji": (str, _MAX_EMOJI)},
    "remove_reaction":  {"message_id": (str, _MAX_ID), "emoji": (str, _MAX_EMOJI)},
    "set_member_role":  {"room_id": (str, _MAX_ID), "webid": (str, _MAX_ID), "role": (str, 32, _is_valid_role)},
    "transfer_ownership": {"room_id": (str, _MAX_ID), "to_did": (str, _MAX_ID)},
    # update_room: name and topic are optional; the handler checks their length.
    "update_room":      {"room_id": (str, _MAX_ID)},
    "revoke_session":   {"session_id": (str, _MAX_ID)},
    "create_webhook":   {"bot_name": (str, 32), "direction": (str, 16, _is_valid_direction)},
    "connect_css":      {"css_url": (str, 256, _is_https_url), "email": (str, 254)},
    "resolve_did":      {"did": (str, 256, _is_did_key)},
    # Hole punch (Round 24 — actor-bound, mutating)
    "request_hole_punch": {"peer_webid": (str, 256)},
    "hole_punch_complete_notify": {
        "attempt_id": (str, 64),
        "result": (str, 16),
    },
}

# Optional per-command id fields that must be path-safe WHEN PRESENT, because a
# handler embeds them in a pod resource path. This backstops the handler guard
# without making the field required (a client may omit message_id and let the
# gateway mint one) and without changing the "ignore undeclared" rule for any
# other command.
_PATH_SAFE_ID_RE = _re.compile(r"^[\w.-]{1,128}$")
_OPTIONAL_PATH_IDS: dict[str, frozenset] = {
    "local_dm": frozenset({"message_id"}),
    "send_dm":  frozenset({"message_id"}),
}

# Every WebSocket command ProxionGateway.process_command dispatches is listed in
# exactly one of MUTATING_COMMANDS or READ_ONLY_COMMANDS
# (tests/security/test_command_classification.py parses the dispatcher and fails
# on any command that is in neither, so a new command can't skip the gates).
#
# MUTATING: writes persistent state, changes identity/keys/settings/membership,
# or delivers content or signals to another user. A revoked identity, safe mode
# and a failed DB integrity check all block these (except TEARDOWN_COMMANDS).
MUTATING_COMMANDS: frozenset[str] = frozenset({
    # messages and content
    "send_dm", "send_room", "local_dm", "sealed_dm", "send_dm_fanout",
    "edit_message", "edit_local_message", "delete_local_message", "forward_message",
    "send_file", "file_offer", "file_accept", "file_reject", "file_chunk", "file_complete",
    "file_received",
    "send_voice_message", "schedule_message", "cancel_scheduled",
    "add_reaction", "remove_reaction", "pin_message", "unpin_message",
    "set_disappear_timer",
    # rooms
    "chat_room_create", "rehost_room", "join_room", "join_remote_room", "join_by_invite", "create_invite",
    "announce_room_join", "leave_local_room", "delete_room", "update_room",
    "transfer_ownership", "accept_ownership", "decline_ownership",
    "kick_member", "ban_member", "unban_member", "mute_member", "unmute_member",
    "set_member_role", "add_room_emoji", "remove_room_emoji",
    # contacts and trust
    "send_friend_request", "accept_friend_request", "decline_friend_request", "restore_contacts",
    "revoke_contact", "block", "unblock", "verify_contact",
    "apply_contact_verification_sync", "sync_contact_verifications",
    "approve_peer_gateway_change", "resolve_peer_trust_dispute",
    # calls and connectivity
    "voice_invite", "voice_answer", "ice_candidate", "voice_hangup",
    "join_voice_channel", "leave_voice_channel",
    "request_hole_punch", "hole_punch_complete_notify",
    "start_tunnel", "stop_tunnel",
    # identity, keys, devices
    "set_identity", "upload_prekeys", "get_prekey_bundle", "rotate_spk",
    "upload_sender_key", "distribute_sender_key", "ack_sender_key_rotation",
    "register_device", "unregister_device", "set_primary_device", "revoke_device_and_rekey",
    "pair_start", "pair_submit", "pair_approve", "pair_cancel",
    "device_recovery_code_generate", "device_recovery_code_use",
    "expire_dm_session", "revoke_session", "logout_all_devices", "delete_account",
    # pod, push, webhooks, settings
    "connect_css", "disconnect_pod", "reconnect_pod",
    "subscribe_push", "unsubscribe_push",
    "create_webhook", "delete_webhook", "rotate_webhook",
    "set_thread_mute", "set_receipts_enabled", "set_link_previews_enabled",
    "set_account_pref",
    # owner security operations
    "prepare_recovery_operation", "confirm_recovery_operation",
    "release_quarantine_item", "drop_quarantine_item", "ack_checksum_mismatch",
    "set_security_tier", "set_retention_lock", "clear_retention_lock",
    "request_admin_action", "confirm_admin_action",
    "create_trust_revocation", "start_compromise_recovery",
    "resume_compromise_recovery", "abort_compromise_recovery",
    "run_recovery_drill",
})

# Mutating commands that only reduce exposure (sign out, hang up, leave, block,
# stop sharing). A revoked identity and safe mode must still be able to run
# these, so they skip those two gates.
TEARDOWN_COMMANDS: frozenset[str] = frozenset({
    "voice_hangup", "leave_voice_channel", "leave_local_room",
    "stop_tunnel", "disconnect_pod", "unsubscribe_push", "delete_webhook",
    "revoke_session", "logout_all_devices", "unregister_device", "pair_cancel",
    "expire_dm_session", "block", "revoke_contact", "decline_ownership",
})

# READ_ONLY: queries, the auth handshake, and caller-scoped bookkeeping the
# client needs in order to read (read markers, delivery acks, ratchet session
# upkeep, typing/screen-share notices inside an existing call or thread).
READ_ONLY_COMMANDS: frozenset[str] = frozenset({
    "register", "auth_response", "link_pod", "ping",
    "get_rooms", "get_dms", "get_local_history", "get_room_members", "get_message",
    "get_files", "export_ticket",
    "read_dm", "read_room", "search", "get_receipts", "get_message_readers",
    "get_pins", "get_room_bans", "get_room_roles", "list_room_emoji",
    "get_disappear_timer", "list_scheduled", "get_notifications",
    "resolve_did", "discover_peer", "get_identity", "get_connect_id",
    "resolve_connect_id", "get_my_address", "get_gateway_webid", "get_inbox_webhook",
    "get_relationships", "list_friend_requests", "list_blocks",
    "get_contact_verification", "list_verified_contacts",
    "get_presence", "get_all_presence", "set_presence", "typing",
    "get_voice_channel_state", "screenshare_started", "screenshare_stopped",
    "tunnel_status", "pod_status", "list_sessions", "list_webhooks",
    "list_devices", "get_peer_devices", "get_peer_device_keys", "get_sender_key",
    "list_dm_sessions", "get_account_sync",
    # caller-scoped bookkeeping
    "mark_read", "update_last_read", "ack_delivered", "ack_read",
    "mark_notification_read", "catch_up", "catch_up_ack",
    "session_unknown", "session_ready", "save_session_state", "dm_decrypt_failed",
    # owner security reads and reports
    "get_audit_logs", "get_security_events", "get_runtime_security_state",
    "get_security_summary", "get_degraded_mode_state", "get_realtime_abuse_signals",
    "export_security_snapshot", "list_quarantine_items", "get_security_tier_state",
    "list_retention_locks", "run_security_self_test", "simulate_incident_policy",
    "list_trust_revocations", "get_compromise_recovery_status",
    "get_security_event_stream", "get_solid_migration_errors",
    "get_access_grants_policy_state", "get_security_exit_gate_status",
    "list_recovery_drill_templates", "get_recovery_drill_report",
})

# Cheap reads a client sends in bursts: the connect snapshot and the lookups
# behind opening a room or DM. They use their own, larger per-connection budget
# (gateway._check_read_rate_limit) instead of the general 60/10s one, so a big
# account's connect or quick room switching is not refused, and they cannot
# use up the budget that sends and other writes rely on. Nothing here writes
# shared state or reaches another user or gateway (mark_read, typing and
# set_presence broadcast, search is heavy, so those stay on the general budget).
SNAPSHOT_READ_COMMANDS: frozenset[str] = frozenset({
    "get_rooms", "get_dms", "get_local_history", "get_room_members", "get_message",
    "get_pins", "get_receipts", "get_message_readers", "get_room_bans",
    "get_room_roles", "list_room_emoji", "get_disappear_timer", "list_scheduled",
    "get_notifications", "get_identity", "get_connect_id", "get_my_address",
    "get_gateway_webid", "get_inbox_webhook", "get_relationships",
    "list_friend_requests", "list_blocks", "get_contact_verification",
    "list_verified_contacts", "get_presence", "get_all_presence",
    "get_voice_channel_state", "tunnel_status", "pod_status", "list_sessions",
    "list_webhooks", "list_devices", "get_peer_devices", "get_peer_device_keys",
    "get_sender_key", "list_dm_sessions", "update_last_read", "get_account_sync",
})
assert SNAPSHOT_READ_COMMANDS <= READ_ONLY_COMMANDS


def blocked_when_locked(cmd: str) -> bool:
    """True when a revoked identity or safe mode must refuse *cmd*."""
    return cmd in MUTATING_COMMANDS and cmd not in TEARDOWN_COMMANDS

# Commands that are auth-rate-limited (5 per minute per socket)
AUTH_RATE_COMMANDS: frozenset[str] = frozenset({"register", "auth_response"})

# Commands that count against the heavy rate limit (10 per minute per socket)
HEAVY_COMMANDS: frozenset[str] = frozenset({
    "search", "send_file", "send_voice_message", "voice_invite",
    "schedule_message", "restore_contacts", "request_hole_punch",
})


# E2E-sealed DM edits, reactions and files (docs/E2E_ENCRYPTION.md, web
# e2e-secondary.js). The browser encrypts these with the DM ratchet; the gateway
# only checks the header shape and forwards it. It never sees the plaintext.
SEALED_V = 1
SEALED_KEYS = ("e2e", "e2e_v", "content", "nonce", "msg_num", "pn", "ratchet_pub")
_B64U_RE = _re.compile(r"^[A-Za-z0-9_-]+$")


def sealed_fields(data: dict, max_content: int = _MAX_CONTENT):
    """Validated sealed header of a DM edit, reaction or file command or relay.

    Returns {} when *data* carries none (plaintext, as older clients send), the
    header fields when it carries a valid v1 header, and None when it claims one
    that is malformed or of an unknown version (callers reject those).
    """
    if not isinstance(data, dict):
        return None
    if data.get("e2e") is not True and data.get("e2e_v") is None:
        return {}
    e2e_v = data.get("e2e_v")
    if data.get("e2e") is not True or type(e2e_v) is not int or e2e_v != SEALED_V:
        return None

    def _b64u(v, cap):
        return isinstance(v, str) and 0 < len(v) <= cap and bool(_B64U_RE.match(v))

    def _num(v):
        return type(v) is int and 0 <= v < 2 ** 31

    content, nonce, rpub = data.get("content"), data.get("nonce"), data.get("ratchet_pub")
    msg_num, pn = data.get("msg_num"), data.get("pn", 0)
    if not (_b64u(content, max_content) and _b64u(nonce, 32) and _b64u(rpub, 64)
            and _num(msg_num) and _num(pn)):
        return None
    return {"e2e": True, "e2e_v": SEALED_V, "content": content, "nonce": nonce,
            "msg_num": msg_num, "pn": pn, "ratchet_pub": rpub}


def sealed_header(fields: dict) -> dict:
    """The sealed fields minus the ciphertext (for events that carry it elsewhere)."""
    return {k: v for k, v in (fields or {}).items() if k != "content"}


class SchemaError(ValueError):
    """Raised when a command payload violates the declared schema."""


def validate_command_payload(cmd: str, data: dict) -> None:
    """Raise SchemaError if *data* violates the schema for *cmd*.

    Unknown commands pass through — the routing dispatcher rejects them.
    Only validates fields that are declared in _SCHEMA; extra fields are ignored.
    Schema tuples: (expected_type, max_len_or_None[, validator_fn])
    """
    schema = _SCHEMA.get(cmd)
    if schema is None:
        return

    # Apply NFC normalization to user-visible text fields
    for field in _TEXT_FIELDS:
        if field in data and isinstance(data.get(field), str):
            data[field] = normalize_text_field(data[field])

    # Reject floats masquerading as integers for integer fields
    _INT_FIELDS = frozenset({"limit", "duration_ms", "ms", "max_uses", "expires_hours", "seq_num"})
    for _ifield in _INT_FIELDS:
        if _ifield in data:
            _v = data[_ifield]
            if isinstance(_v, bool):
                raise SchemaError(f"{cmd}.{_ifield}: must be integer, not bool")
            if isinstance(_v, float):
                raise SchemaError(f"{cmd}.{_ifield}: must be integer, not float")

    # Reject non-bool for boolean toggle fields
    _BOOL_FIELDS = frozenset({"enabled"})
    for _bfield in _BOOL_FIELDS:
        if _bfield in data and not isinstance(data[_bfield], bool):
            raise SchemaError(f"{cmd}.{_bfield}: must be boolean")

    for field, spec in schema.items():
        expected_type, max_len = spec[0], spec[1]
        validator = spec[2] if len(spec) > 2 else None
        value = data.get(field)
        if value is None:
            raise SchemaError(f"{cmd}.{field}: required field missing")
        if not isinstance(value, expected_type):
            raise SchemaError(
                f"{cmd}.{field}: expected {expected_type.__name__}, "
                f"got {type(value).__name__}"
            )
        if max_len is not None:
            raw = value.encode("utf-8") if isinstance(value, str) else value
            if len(raw) > max_len:
                raise SchemaError(
                    f"{cmd}.{field}: payload exceeds max length {max_len}"
                )
        if validator is not None and not validator(value):
            raise SchemaError(f"{cmd}.{field}: invalid value")

    # Optional path-safe id fields: reject a traversal id before a handler can
    # embed it in a pod resource path (see _OPTIONAL_PATH_IDS).
    for _idf in _OPTIONAL_PATH_IDS.get(cmd, ()):  # noqa: B007
        _idv = data.get(_idf)
        if _idv in (None, ""):
            continue
        if (not isinstance(_idv, str)
                or not _PATH_SAFE_ID_RE.match(_idv)
                or _idv.startswith(".")):
            raise SchemaError(f"{cmd}.{_idf}: unsafe id")
