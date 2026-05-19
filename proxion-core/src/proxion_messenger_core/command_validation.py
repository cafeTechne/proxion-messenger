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

_TEXT_FIELDS = frozenset({"display_name", "room_name", "name", "status_message", "bot_name"})
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
    # register accepts either "did" OR "webid" — handler validates internally
    "auth_response":    {"signature": (str, _MAX_SIG)},
    "join_room":        {"code": (str, _MAX_ID)},
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
    "transfer_ownership": {"room_id": (str, _MAX_ID), "to_webid": (str, _MAX_ID)},
    "revoke_session":   {"session_id": (str, _MAX_ID)},
    "create_webhook":   {"bot_name": (str, 32), "direction": (str, 16, _is_valid_direction)},
    "connect_css":      {"css_url": (str, 256, _is_https_url), "email": (str, 254)},
    "resolve_did":      {"did": (str, 256, _is_did_key)},
}

# Commands that mutate state and should require auth / revocation check
MUTATING_COMMANDS: frozenset[str] = frozenset({
    "send_dm", "send_room", "local_dm", "edit_message", "send_file",
    "schedule_message", "delete_local_message", "forward_message",
    "add_reaction", "remove_reaction", "pin_message", "unpin_message",
    "kick_member", "delete_room", "set_member_role", "transfer_ownership",
    "block", "unblock", "set_disappear_timer", "send_voice_message",
})

# Commands that are auth-rate-limited (5 per minute per socket)
AUTH_RATE_COMMANDS: frozenset[str] = frozenset({"register", "auth_response"})

# Commands that count against the heavy rate limit (10 per minute per socket)
HEAVY_COMMANDS: frozenset[str] = frozenset({
    "search", "send_file", "send_voice_message", "voice_invite",
    "schedule_message", "restore_contacts",
})


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
