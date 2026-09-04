// roomdesc.js — the canonical room descriptor (PLAN_ROUND_71 B1).
//
// Under the chosen authority model (B), the gateway is authoritative for LIVE
// governance and the pod holds a durable, host-owned RECORD a gateway can
// rehydrate a room from when it has no state for it. This is that record: a plain
// JSON document at rooms/{id}/room.json (the same place podWriteRoomMeta already
// uses), describing a room's identity, owner, members with roles, bans, and a
// pointer to its Long Chat. Pure build/parse/normalise here; pod I/O and (B3)
// signing live elsewhere.

export const ROOM_DESC_VERSION = 1;
const ROLES = new Set(['owner', 'admin', 'member']);
// A descriptor rides in an attacker-writable room.json; cap membership so a huge
// members array can't blow up here. Matches the gateway's _MAX_ROOM_MEMBERS_ACL.
const MAX_MEMBERS = 500;

/**
 * Normalise a members list into descriptor form: deduped by webid, a valid role
 * each, the owner always present and always role "owner". Accepts either strings
 * (webids) or { webid, role, banned } objects.
 */
export function normalizeMembers(members, owner) {
    const out = [];
    const seen = new Set();
    for (const m of members || []) {
        if (out.length >= MAX_MEMBERS) break;
        const webid = typeof m === 'string' ? m : (m && m.webid);
        if (!webid || seen.has(webid)) continue;
        seen.add(webid);
        let role = (m && typeof m === 'object' && m.role) || 'member';
        if (!ROLES.has(role)) role = 'member';
        if (webid === owner) role = 'owner';
        out.push({ webid, role, banned: !!(m && typeof m === 'object' && m.banned) });
    }
    if (owner && !seen.has(owner)) out.unshift({ webid: owner, role: 'owner', banned: false });
    return out;
}

/** Build a canonical descriptor. `longChat` (the container URL) is optional here;
 *  the pod writer fills it in from the room id when omitted. */
export function buildRoomDescriptor({ roomId, title, owner, members, longChat, created, updated } = {}) {
    const now = new Date().toISOString();
    return {
        'px:type': 'RoomDescriptor',
        'px:version': ROOM_DESC_VERSION,
        room_id: String(roomId || ''),
        title: title != null ? String(title) : '',
        owner: String(owner || ''),
        members: normalizeMembers(members, String(owner || '')),
        long_chat: longChat != null ? String(longChat) : '',
        created: created || now,
        updated: updated || now,
    };
}

/** A copy of `descriptor` with its membership replaced (owner preserved). */
export function withMembers(descriptor, members) {
    return {
        ...descriptor,
        members: normalizeMembers(members, descriptor.owner),
        updated: new Date().toISOString(),
    };
}

// ── Signing (B3) ──────────────────────────────────────────────────────────────
//
// The exact bytes signed over a descriptor, mirroring the gateway's
// room_descriptor.canonical_bytes (length-prefixed UTF-8 parts joined by '|', the
// device-cert scheme). long_chat and updated are NOT signed (long_chat is filled
// server-side after signing; updated is not security relevant). main.js signs these
// bytes with the client's Ed25519 key and attaches px:signer + px:sig.
const _ENC = new TextEncoder();
const _US = String.fromCharCode(0x1f);   // webid/role separator (0x1f, matches Python)
const _RS = String.fromCharCode(0x1e);   // between-members separator (0x1e, matches Python)

function _lengthPrefixed(parts) {
    const chunks = parts.map((p) => {
        const c = new Uint8Array(2 + p.length);
        c[0] = (p.length >> 8) & 0xff;
        c[1] = p.length & 0xff;
        c.set(p, 2);
        return c;
    });
    const total = chunks.reduce((a, c) => a + c.length, 0) + Math.max(0, chunks.length - 1);
    const out = new Uint8Array(total);
    let off = 0;
    chunks.forEach((c, i) => {
        if (i > 0) out[off++] = 0x7c;   // '|'
        out.set(c, off);
        off += c.length;
    });
    return out;
}

export function descriptorSigningBytes(desc) {
    const members = (desc.members || [])
        .filter((m) => m && m.webid)
        .map((m) => `${m.webid}${_US}${m.role || ''}`)
        .sort();
    const parts = [
        'proxion-room-descriptor-v1',
        String(desc.room_id || ''),
        String(desc.owner || ''),
        String(desc.created || ''),
        members.join(_RS),
    ].map((s) => _ENC.encode(s));
    return _lengthPrefixed(parts);
}

/** Parse + validate a stored descriptor; null if it is not a usable one. */
export function parseRoomDescriptor(json) {
    if (!json || typeof json !== 'object') return null;
    if (!json.room_id || !json.owner) return null;
    const owner = String(json.owner);
    return {
        'px:type': 'RoomDescriptor',
        'px:version': Number(json['px:version']) || 1,
        room_id: String(json.room_id),
        title: json.title != null ? String(json.title) : '',
        owner,
        members: normalizeMembers(json.members, owner),
        long_chat: json.long_chat != null ? String(json.long_chat) : '',
        created: json.created || null,
        updated: json.updated || null,
    };
}
