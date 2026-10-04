// errors.js: turn raw gateway error strings into friendly, translated copy.
//
// The gateway sends either a stable code ("room_full") or, on older paths, an
// English sentence ("No room found with invite code ..."). Neither should reach
// the user as-is: known ones map to an i18n key, anything else becomes a
// generic "Something went wrong" and the raw text goes to the console.

// Exact backend error string -> i18n key.
const EXACT = {
    "empty_content": "error.empty_content",
    "content_too_large": "error.content_too_large",
    "invalid_sequence": "error.invalid_sequence",
    "file_too_large": "error.file_too_large",
    "chunk_too_large": "error.chunk_too_large",
    "not_a_room_member": "error.not_a_room_member",
    "banned_from_room": "error.banned_from_room",
    "invalid_code": "error.invalid_code",
    "room_not_found": "error.room_not_found",
    "room_full": "error.room_full",
    "invite_expired_or_exhausted": "error.invite_expired_or_exhausted",
    "join_rate_limited": "error.join_rate_limited",
    "call_too_frequent": "error.call_too_frequent",
    "voice_invite_not_allowed": "error.voice_invite_not_allowed",
    "voice_sessions_full": "error.voice_sessions_full",
    "voice_note_remote_unsupported": "error.voice_note_remote_unsupported",
    "reaction_limit_reached": "error.reaction_limit_reached",
    "contact_revoked": "error.contact_revoked",
    "Not registered": "error.not_registered",
    "send_at must be in the future": "error.send_at_future",
    "Cannot delete another user's message": "error.cannot_delete_others",
    "Cannot edit another user's message": "error.cannot_edit_others",
};

// Prefixes of backend sentences that carry a dynamic tail (a code, a type).
const PREFIXES = [
    ["No room found with invite code", "error.inviteNotFound"],
    ["file_type_not_allowed", "error.file_type_not_allowed"],
];

export const GENERIC_ERROR_KEY = "error.generic";

// The i18n key for a raw gateway error, or null when it is unknown.
export function knownErrorKey(raw) {
    const s = String(raw || "");
    if (Object.prototype.hasOwnProperty.call(EXACT, s)) return EXACT[s];
    for (const [prefix, key] of PREFIXES) if (s.startsWith(prefix)) return key;
    return null;
}

// Always returns a key; logs the raw text when it falls back to the generic one.
export function friendlyErrorKey(raw) {
    const key = knownErrorKey(raw);
    if (key) return key;
    console.warn("[Proxion] unmapped gateway error:", raw);
    return GENERIC_ERROR_KEY;
}
