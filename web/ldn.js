// ldn.js — Linked Data Notifications (W3C Rec) for cross-app chat invitations.
// Pure protocol, no I/O: build the invite notification we POST to a recipient's
// ldp:inbox, and parse an inbox listing / a single notification coming back.
//
// An invite is an ActivityStreams `Invite`: actor = the sender's WebID, object =
// the chat container (a Link carrying the human title). Because it is a standard
// AS2 activity dropped in a standard ldp:inbox, any Solid app can send us one and
// read ours. We parse liberally (compacted OR expanded JSON-LD, our shape or
// another app's) but only ever surface notifications that reference a *container*,
// so unrelated inbox traffic is ignored rather than misread as a chat invite.

export const AS = 'https://www.w3.org/ns/activitystreams#';
export const LDP = 'http://www.w3.org/ns/ldp#';
export const INBOX_PRED = LDP + 'inbox';
export const CONTAINS_PRED = LDP + 'contains';

/** The JSON-LD invite we POST to a recipient's inbox. */
export function buildInviteNotification({ from, to, container, title = '', published } = {}) {
    return {
        '@context': 'https://www.w3.org/ns/activitystreams',
        type: 'Invite',
        actor: String(from || ''),
        target: String(to || ''),
        object: {
            type: 'Link',
            href: String(container || ''),
            name: String(title || ''),
        },
        published: published || new Date().toISOString(),
    };
}

// ── Reading ──────────────────────────────────────────────────────────────────

// Cap on nodes walked from a (public-Append, attacker-writable) inbox / drop-box
// listing: defence in depth alongside the byte cap on the fetched body in pod.js.
// Mirrors parseLongChatJsonLd's slice.
const MAX_INBOX_NODES = 5000;

function nodesOf(json) {
    if (!json) return [];
    if (Array.isArray(json)) return json;
    if (Array.isArray(json['@graph'])) return json['@graph'];
    return [json];
}

// Raw values for any of `keys` on a node (each key may be a compacted term or an
// expanded IRI). Always returns a flat array.
function vals(node, keys) {
    const out = [];
    for (const k of keys) {
        const raw = node[k];
        if (raw == null) continue;
        (Array.isArray(raw) ? raw : [raw]).forEach(v => out.push(v));
    }
    return out;
}

// Coerce a value to an IRI: an @id/id/href object, or a bare string.
function asId(v) {
    if (v && typeof v === 'object') return v['@id'] || v.id || v.href || null;
    return typeof v === 'string' ? v : null;
}
// Coerce a value to a literal string: a bare string or an @value object.
function asStr(v) {
    if (v && typeof v === 'object') {
        if ('@value' in v) return typeof v['@value'] === 'string' ? v['@value'] : null;
        return null;
    }
    return typeof v === 'string' ? v : null;
}
function firstId(arr) { for (const v of arr) { const id = asId(v); if (id) return id; } return null; }
function firstStr(arr) { for (const v of arr) { const s = asStr(v); if (s) return s; } return null; }

function resolve(base, ref) {
    try { return new URL(ref, base).href; } catch { return ref; }
}

/**
 * Whether the invite's `actor` can be trusted as the inviter. The ldp:inbox is
 * public-Append, so anyone may drop an AS2 Invite naming any `actor`. A sender
 * can only legitimately invite you to a container on their own pod, so we treat
 * the invite as verified only when the actor WebID and the container share an
 * origin. A missing actor or a cross-origin container is unverified (a spoof
 * signal); the UI surfaces that rather than presenting the actor as trusted.
 */
export function inviteActorVerified(from, container) {
    try {
        return new URL(String(from)).origin === new URL(String(container)).origin;
    } catch { return false; }
}

/**
 * Notification resource URLs listed in an inbox container document (JSON-LD),
 * from `ldp:contains`. Relative refs are resolved against the inbox URL.
 */
export function parseInboxListing(json, inboxUrl = '') {
    const out = [];
    const seen = new Set();
    for (const node of nodesOf(json).slice(0, MAX_INBOX_NODES)) {
        if (!node || typeof node !== 'object') continue;
        for (const v of vals(node, ['contains', CONTAINS_PRED])) {
            const id = asId(v);
            if (!id) continue;
            const abs = inboxUrl ? resolve(inboxUrl, id) : id;
            if (!seen.has(abs)) { seen.add(abs); out.push(abs); }
        }
    }
    return out;
}

/**
 * Parse a single notification into { from, container, title, verified }, or null
 * if it does not reference a chat container. `object` may be an IRI or a nested
 * Link with href/name; we also fall back to top-level href/target and
 * name/summary so an invite framed by another app still resolves. `verified` is
 * a provenance signal (see inviteActorVerified): the inbox is public-Append, so
 * a caller must not present `from` as a trusted identity when it is false.
 */
export function parseInviteNotification(json) {
    for (const node of nodesOf(json)) {
        if (!node || typeof node !== 'object') continue;
        const from = firstId(vals(node, ['actor', AS + 'actor']));
        let container = null;
        let title = null;

        for (const o of vals(node, ['object', AS + 'object'])) {
            let id = asId(o);
            if (!id && o && typeof o === 'object') id = firstId(vals(o, ['href', AS + 'href', '@id', 'id']));
            if (id && id.endsWith('/')) container = container || id;
            if (o && typeof o === 'object') {
                const n = firstStr(vals(o, ['name', AS + 'name', 'summary', AS + 'summary']));
                if (n) title = title || n;
            }
        }
        if (!container) {
            const id = firstId(vals(node, ['href', AS + 'href', 'target', AS + 'target']));
            if (id && id.endsWith('/')) container = id;
        }
        if (!title) title = firstStr(vals(node, ['name', AS + 'name', 'summary', AS + 'summary']));

        if (container) return { from: from || '', container, title: title || '', verified: inviteActorVerified(from, container) };
    }
    return null;
}
