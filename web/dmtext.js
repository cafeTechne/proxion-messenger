// dmtext.js: the text a DM message shows, never its E2E envelope.
//
// The gateway keeps an E2E DM only as ratchet ciphertext it cannot read, and the
// forward ratchet cannot open it a second time. So every surface that shows a
// DM body from a gateway copy (history, pins, search hits, reply quotes,
// notifications) asks here: this device's plaintext copy if it has one, else the
// UNDECRYPTABLE placeholder, which each surface renders as a localized notice.

import { UNDECRYPTABLE, isUndecryptable } from './e2e-secondary.js';

// Gateways before the content_type marker stored sealed rows with no flag.
// Ratchet ciphertext is unpadded base64url of at least 17 bytes (tag + text):
// one 23+ character token with no spaces. Require mixed case plus a digit or
// - / _ so ordinary long words never match. Used only for unflagged DM rows.
const LEGACY_CT = /^[A-Za-z0-9_-]{23,}$/;
export function looksLikeCiphertext(text) {
    const s = String(text || '');
    return LEGACY_CT.test(s) && /[a-z]/.test(s) && /[A-Z]/.test(s) && /[0-9_-]/.test(s);
}

// True when a gateway/pod copy of a message is still sealed.
//   m.e2e === true or content_type 'e2e': the gateway says so.
//   m.e2e === false: this device already opened (or wrote) it.
//   otherwise, in a DM thread only: the legacy ciphertext shape.
export function isSealedCopy(m, isDm = false) {
    if (!m) return false;
    if (m.e2e === true || m.content_type === 'e2e') return true;
    if (m.e2e === false) return false;
    return !!isDm && looksLikeCiphertext(m.content);
}

// A usable plaintext: a string that is not a placeholder.
function usable(text) {
    return typeof text === 'string' && !isUndecryptable(text);
}

// The text to show for one message: the cached plaintext when there is one,
// else UNDECRYPTABLE for a sealed copy, else its own content.
export function dmDisplayText(m, { isDm = false, plaintext = null } = {}) {
    if (usable(plaintext)) return plaintext;
    if (isSealedCopy(m, isDm)) return UNDECRYPTABLE;
    return m && m.content != null ? String(m.content) : '';
}

// A copy of `m` safe to put in the feed: a sealed body becomes `plaintext`
// (when known) or UNDECRYPTABLE, and the e2e flag is cleared so later code
// treats it as opened text. Unsealed messages come back unchanged.
export function unsealCopy(m, { isDm = false, plaintext = null } = {}) {
    if (!m || !isSealedCopy(m, isDm)) return m;
    const { content_type: _ct, ...rest } = m;
    return { ...rest, content: dmDisplayText(m, { isDm, plaintext }), e2e: false };
}

// Async form for lists that do not render in the feed (pins, search hits).
// lookup(m) returns this device's plaintext for m (or null); it is only asked
// about sealed copies. Each item comes back as unsealCopy would make it.
export async function unsealList(list, { isDm = () => false, lookup = async () => null } = {}) {
    const out = [];
    for (const m of list || []) {
        const dm = !!isDm(m);
        if (!isSealedCopy(m, dm)) { out.push(m); continue; }
        let plain = null;
        try { plain = await lookup(m); } catch (_) { plain = null; }
        out.push(unsealCopy(m, { isDm: dm, plaintext: plain }));
    }
    return out;
}

// Search hits: drop gateway hits whose text is still sealed (a match against
// ciphertext is noise), then add this device's own plaintext matches that the
// gateway could not see. Gateway order first, local hits after, no duplicates.
export function mergeSearchHits(gatewayHits, localHits, { isDm = () => false } = {}) {
    const out = [];
    const seen = new Set();
    for (const h of gatewayHits || []) {
        if (isSealedCopy(h, !!isDm(h))) continue;
        out.push(h);
        if (h.message_id) seen.add(h.message_id);
    }
    for (const h of localHits || []) {
        if (!h || !h.message_id || seen.has(h.message_id)) continue;
        seen.add(h.message_id);
        out.push(h);
    }
    return out;
}

// Short text for a notification, preview or quote: the localized notice for a
// placeholder or a still-sealed body, else the text.
export function previewText(m, notice, isDm = false) {
    if (!m) return '';
    if (isSealedCopy(m, isDm) || isUndecryptable(m.content)) return notice;
    return m.content != null ? String(m.content) : '';
}
