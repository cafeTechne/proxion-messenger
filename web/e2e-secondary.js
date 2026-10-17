// e2e-secondary.js: end-to-end encryption of DM edits, reactions and files.
//
// A DM message body is a ratchet message (e2e.js). The events that change or
// decorate a message (an edit, a reaction, a file attachment) are sealed the
// same way: each one is its own ratchet message on the same per-peer session,
// carrying the header fields the message path uses (content, nonce, msg_num,
// pn, ratchet_pub) plus `e2e: true` and `e2e_v: 1`.
//
// The ratchet plaintext of a sealed event is SEALED_PREFIX followed by JSON
// with a kind `k` and the id of the message it applies to, so a gateway cannot
// replay one kind as another or point an edit at a different message.
//
// Files: the bytes are encrypted with a fresh AES-256-GCM key per file and
// travel through the normal file transport as ciphertext. The key, nonce and
// metadata (name, type, size, dimensions, spoiler) go inside the sealed
// ratchet message.
//
// Reactions: the gateway stores and relays an opaque random token in place of
// the emoji; the sealed message maps the token to the emoji. Removal sends the
// same token. This device remembers token -> emoji so stored reactions render
// after a reload.
//
// Everything here is pure (WebCrypto + injected encrypt/decrypt and storage) so
// it is unit-testable in Node.

export const SEALED_V = 1;
export const SEALED_PREFIX = '\u0000pxs1';
export const KIND_EDIT = 'edit';
export const KIND_REACT = 'react';
export const KIND_FILE = 'file';
// AES-GCM tag length added to every sealed file.
export const FILE_TAG_BYTES = 16;
// What a sealed file event shows to anyone without the key.
export const SEALED_FILE_NAME = 'file';
export const SEALED_FILE_MIME = 'application/octet-stream';

export class SealedError extends Error {
    constructor(msg) { super(msg); this.name = 'SealedError'; }
}

function b64uEnc(bytes) {
    let s = '';
    for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function b64uDec(s) {
    s = String(s).replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    return Uint8Array.from(atob(s), c => c.charCodeAt(0));
}

// ── Sealed plaintext ─────────────────────────────────────────────────────────

export function encodeSealedPlain(kind, body) {
    return SEALED_PREFIX + JSON.stringify({ ...body, v: SEALED_V, k: kind });
}

// True when a decrypted ratchet plaintext is a sealed secondary payload. The
// message path uses it to refuse a sealed payload delivered as a message.
export function isSealedPlain(text) {
    return typeof text === 'string' && text.startsWith(SEALED_PREFIX);
}

// Parse a sealed plaintext of the expected kind; null when it is anything else.
export function decodeSealedPlain(text, kind) {
    if (!isSealedPlain(text)) return null;
    let obj;
    try { obj = JSON.parse(text.slice(SEALED_PREFIX.length)); } catch { return null; }
    if (!obj || typeof obj !== 'object' || obj.v !== SEALED_V || obj.k !== kind) return null;
    return obj;
}

// ── Header handling ──────────────────────────────────────────────────────────

// Seal `body` as a `kind` event for peerId. `encrypt` is e2e.js ratchetEncrypt.
// Throws when encryption fails; callers must then not send at all.
export async function sealSecondary(encrypt, peerId, kind, body) {
    const enc = await encrypt(peerId, encodeSealedPlain(kind, body));
    return {
        e2e: true, e2e_v: SEALED_V, content: enc.ciphertext, nonce: enc.nonce,
        msg_num: enc.msgNum, pn: enc.pn, ratchet_pub: enc.ratchetPub,
    };
}

// The header fields of a sealed event, for copying into an outgoing command.
export const SEALED_FIELDS = ['e2e', 'e2e_v', 'content', 'nonce', 'msg_num', 'pn', 'ratchet_pub'];

// 'sealed' when ev carries a v1 sealed header, 'legacy' when it carries none
// (an older client's plaintext event), 'unsupported' when it claims encryption
// in a form this client cannot read (a newer version, or a broken header).
// `field` names where the ciphertext is (edit events carry it in new_content).
export function sealedState(ev, field = 'content') {
    if (!ev || typeof ev !== 'object') return 'legacy';
    const claims = ev.e2e === true || ev.e2e_v != null;
    if (!claims) return 'legacy';
    if (ev.e2e_v !== SEALED_V || ev.e2e !== true) return 'unsupported';
    const okStr = (s) => typeof s === 'string' && s.length > 0;
    const okInt = (n) => Number.isInteger(n) && n >= 0;
    if (!okStr(ev[field]) || !okStr(ev.nonce) || !okStr(ev.ratchet_pub)
        || !okInt(ev.msg_num) || !okInt(ev.pn ?? 0)) return 'unsupported';
    return 'sealed';
}

// Decrypt a sealed event from peerId and return its body. `decrypt` is e2e.js
// ratchetDecrypt. `field` names where the ciphertext is (edits carry it in
// new_content on the event). Throws SealedError on any mismatch.
export async function openSecondary(decrypt, peerId, ev, kind, field = 'content') {
    const ct = ev[field];
    const plain = await decrypt(peerId, ct, ev.nonce, ev.msg_num, ev.ratchet_pub, ev.pn ?? 0);
    const body = decodeSealedPlain(plain, kind);
    if (!body) throw new SealedError('not a sealed ' + kind);
    return body;
}

// Key that identifies one ratchet message: our own echo of a sealed event
// carries the same header, so the sender can find what it sent.
export function sealedKey(ev) {
    return ev && ev.ratchet_pub ? ev.ratchet_pub + ':' + ev.msg_num : '';
}

// Plaintext of our own sealed sends, held until the gateway echo arrives (the
// sender cannot decrypt its own ratchet message). Bounded.
export function createSealedOutbox(max = 32) {
    const m = new Map();
    return {
        put(hdr, value) {
            const k = sealedKey(hdr);
            if (!k) return;
            m.set(k, value);
            while (m.size > max) m.delete(m.keys().next().value);
        },
        take(ev) {
            const k = sealedKey(ev);
            if (!k || !m.has(k)) return null;
            const v = m.get(k);
            m.delete(k);
            return v;
        },
        get size() { return m.size; },
    };
}

// ── Edits ────────────────────────────────────────────────────────────────────

export function editBody(messageId, text) {
    return { mid: messageId, text };
}

// Validate an opened edit against the event that carried it.
export function checkEditBody(body, ev) {
    if (!body || body.mid !== ev.message_id || typeof body.text !== 'string') {
        throw new SealedError('edit does not match its event');
    }
    return body.text;
}

// ── Reactions ────────────────────────────────────────────────────────────────

const TOKEN_RE = /^~[A-Za-z0-9_-]{11}$/;
// 12 characters: fits the gateway's emoji field limit.
export function newReactionToken() {
    return '~' + b64uEnc(crypto.getRandomValues(new Uint8Array(8)));
}
export function isReactionToken(s) {
    return typeof s === 'string' && TOKEN_RE.test(s);
}

export function reactionBody(messageId, emoji, token) {
    return { mid: messageId, emoji, tok: token };
}

export function checkReactionBody(body, ev) {
    if (!body || body.mid !== ev.message_id || body.tok !== ev.emoji
        || typeof body.emoji !== 'string' || !body.emoji || body.emoji.length > 64) {
        throw new SealedError('reaction does not match its event');
    }
    return body.emoji;
}

// token -> {e: emoji, m: message id, own: bool}, persisted in `storage`
// (localStorage) under `key`, newest last, capped at `max`.
export function createReactionTokens({ storage, key, max = 2000 }) {
    const k = () => (typeof key === 'function' ? key() : key);
    function load() {
        try {
            const v = JSON.parse(storage.getItem(k()) || '{}');
            return v && typeof v === 'object' ? v : {};
        } catch { return {}; }
    }
    function save(map) {
        const ids = Object.keys(map);
        if (ids.length > max) for (const id of ids.slice(0, ids.length - max)) delete map[id];
        try { storage.setItem(k(), JSON.stringify(map)); } catch { /* quota: best effort */ }
    }
    return {
        get(tok) { return load()[tok] || null; },
        put(tok, rec) { const m = load(); delete m[tok]; m[tok] = rec; save(m); },
        remove(tok) { const m = load(); if (tok in m) { delete m[tok]; save(m); } },
        // Token of our own reaction `emoji` on message `mid`, if any.
        findOwn(mid, emoji) {
            const m = load();
            for (const [t, r] of Object.entries(m)) if (r.own && r.m === mid && r.e === emoji) return t;
            return null;
        },
    };
}

// ── Files ────────────────────────────────────────────────────────────────────

// Same allowlist the gateway enforces on plaintext files. The gateway cannot
// inspect a sealed file, so the receiving client applies it after decryption.
export const FILE_MIMES = new Set([
    'image/jpeg', 'image/png', 'image/gif', 'image/webp',
    'audio/ogg', 'audio/webm', 'audio/mpeg', 'audio/wav',
    'video/mp4', 'video/webm',
    'application/pdf', 'application/zip',
    'text/plain',
]);

// Executable and script signatures the gateway always refuses (_BLOCKED_MAGIC).
const BLOCKED_MAGIC = [
    [0x4d, 0x5a], [0x7f, 0x45, 0x4c, 0x46],
    [0xce, 0xfa, 0xed, 0xfe], [0xcf, 0xfa, 0xed, 0xfe], [0xca, 0xfe, 0xba, 0xbe],
    [0x23, 0x21],
];
export function isBlockedMagic(u8) {
    return BLOCKED_MAGIC.some(sig => sig.every((b, i) => u8[i] === b));
}

export function sanitizeFilename(name) {
    let s = String(name || '').replace(/\\/g, '/');
    s = s.slice(s.lastIndexOf('/') + 1);
    s = Array.from(s).filter(c => c >= ' ' && c !== '\u007f' && !'/<>:"|?*'.includes(c)).join('');
    return s.slice(0, 128) || 'file';
}

// Encrypt file bytes under a fresh AES-256-GCM key. The ciphertext is redrawn
// in the rare case its first bytes look like an executable signature, which the
// gateway's transport check would refuse.
export async function encryptFile(u8) {
    for (;;) {
        const raw = crypto.getRandomValues(new Uint8Array(32));
        const iv = crypto.getRandomValues(new Uint8Array(12));
        const key = await crypto.subtle.importKey('raw', raw, { name: 'AES-GCM' }, false, ['encrypt']);
        const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, u8));
        if (!isBlockedMagic(ct)) return { ct, key: b64uEnc(raw), iv: b64uEnc(iv) };
    }
}

export async function decryptFile(ct, keyB64u, ivB64u) {
    const raw = b64uDec(keyB64u);
    const iv = b64uDec(ivB64u);
    if (raw.length !== 32 || iv.length !== 12) throw new SealedError('bad file key');
    const key = await crypto.subtle.importKey('raw', raw, { name: 'AES-GCM' }, false, ['decrypt']);
    try {
        return new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, ct));
    } catch {
        throw new SealedError('file decryption failed');
    }
}

// Body of a sealed file message.
export function fileBody({ key, iv, name, mime, size, width, height, spoiler }) {
    const b = { key, iv, name, mime, size };
    if (Number.isInteger(width) && Number.isInteger(height)) { b.w = width; b.h = height; }
    if (spoiler === true) b.spoiler = true;
    return b;
}

// Validate an opened file body. Returns normalized metadata or throws.
export function checkFileBody(body) {
    if (!body || typeof body.key !== 'string' || typeof body.iv !== 'string') {
        throw new SealedError('file body missing key');
    }
    const mime = String(body.mime || '').toLowerCase().split(';')[0].trim();
    if (!FILE_MIMES.has(mime)) throw new SealedError('file type not allowed');
    const okDim = (v) => Number.isInteger(v) && v >= 1 && v <= 16384;
    const out = {
        key: body.key, iv: body.iv, filename: sanitizeFilename(body.name), mime_type: mime,
        size: Number.isInteger(body.size) && body.size >= 0 ? body.size : 0,
        spoiler: body.spoiler === true,
    };
    if (okDim(body.w) && okDim(body.h)) { out.width = body.w; out.height = body.h; }
    return out;
}

// Decrypt sealed file bytes and check them like the gateway checks plaintext.
export async function openFileBytes(ct, meta) {
    const u8 = await decryptFile(ct, meta.key, meta.iv);
    if (isBlockedMagic(u8)) throw new SealedError('file type not allowed');
    return u8;
}

function u8ToB64(u8) {
    let s = '';
    for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
    return btoa(s);
}
function b64ToU8(b64) {
    return Uint8Array.from(atob(b64 || ''), c => c.charCodeAt(0));
}

// Text shown for a sealed event this device cannot open (the string the
// message path uses, so the history cache never stores it).
export const UNDECRYPTABLE = '[could not decrypt]';
export const DECRYPT_ERROR = '[decryption error]';

// True for the placeholder strings above. The feed shows a localized notice in
// their place (rendering.js); they stay internal markers, never message text.
export function isUndecryptable(text) {
    return text === UNDECRYPTABLE || text === DECRYPT_ERROR;
}

// ── Send and receive glue ────────────────────────────────────────────────────
//
// deps:
//   encrypt(peerId, text), decrypt(peerId, ct, nonce, msgNum, ratchetPub, pn):
//     e2e.js ratchetEncrypt / ratchetDecrypt
//   isKeyed(peerId): true when an X25519 key is cached for the peer (the same
//     test the message path uses to decide it must encrypt)
//   isSelf(id): true for this user's own identifiers
//   tokens: createReactionTokens(...)
//
// Send side: each seal* returns null when the view is not a DM or the peer has
// no key (the caller then sends what it sent before), and otherwise the sealed
// fields, or throws, in which case the caller must not send.
export function createSecondaryCrypto({ encrypt, decrypt, isKeyed, isSelf, tokens, outbox = createSealedOutbox() }) {
    function peerFor(view) {
        if (!view) return null;
        if (view.type === 'local_dm') return view.peerWebid || view.id || null;
        if (view.type === 'dm') return view.peerWebid || null;
        return null;
    }
    function sealPeer(view) {
        const peer = peerFor(view);
        return peer && isKeyed(peer) ? peer : null;
    }
    // True when sends in this view must be sealed.
    function mustSeal(view) { return !!sealPeer(view); }

    async function sealEdit(view, messageId, text) {
        const peer = sealPeer(view);
        if (!peer) return null;
        const hdr = await sealSecondary(encrypt, peer, KIND_EDIT, editBody(messageId, text));
        outbox.put(hdr, { text });
        return hdr;
    }

    async function sealReaction(view, messageId, emoji) {
        const peer = sealPeer(view);
        if (!peer) return null;
        const tok = newReactionToken();
        const hdr = await sealSecondary(encrypt, peer, KIND_REACT, reactionBody(messageId, emoji, tok));
        tokens.put(tok, { e: emoji, m: messageId, own: true });
        return { ...hdr, emoji: tok };
    }

    // What remove_reaction must name: the token our sealed add used, or the
    // emoji itself for a reaction sent before sealing.
    function reactionWireEmoji(messageId, emoji) {
        return tokens.findOwn(messageId, emoji) || emoji;
    }

    // Encrypt file bytes and seal the key and metadata. Returns {hdr, ct}.
    // `echo` keeps the plaintext for the gateway echo of an inline send.
    async function sealFile(view, u8, meta, { echo = true } = {}) {
        const peer = sealPeer(view);
        if (!peer) return null;
        const { ct, key, iv } = await encryptFile(u8);
        const hdr = await sealSecondary(encrypt, peer, KIND_FILE, fileBody({ key, iv, ...meta }));
        if (echo) {
            const file = { filename: sanitizeFilename(meta.name), mime_type: meta.mime, size: u8.length, data_b64: u8ToB64(u8) };
            if (Number.isInteger(meta.width) && Number.isInteger(meta.height)) { file.width = meta.width; file.height = meta.height; }
            if (meta.spoiler === true) file.spoiler = true;
            outbox.put(hdr, { file });
        }
        return { hdr, ct };
    }

    // Receive side. Each open* returns the event to apply (plaintext in place
    // of ciphertext), or null to drop it.

    async function openEdit(ev) {
        const st = sealedState(ev, 'new_content');
        if (st === 'legacy') return ev;
        if (st === 'unsupported') return null;
        if (isSelf(ev.from_webid)) {
            const mine = outbox.take(ev);
            return mine ? { ...ev, new_content: mine.text, e2e: false } : null;
        }
        if (!ev.from_webid) return null;
        try {
            const body = await openSecondary(decrypt, ev.from_webid, ev, KIND_EDIT, 'new_content');
            return { ...ev, new_content: checkEditBody(body, ev), e2e: false };
        } catch { return null; }
    }

    // A stored or live reaction's emoji field -> the emoji to show, or null to
    // hide it (a token this device never learned).
    function translateReaction(emoji) {
        if (!isReactionToken(emoji)) return emoji;
        const rec = tokens.get(emoji);
        return rec ? rec.e : null;
    }

    async function openReaction(ev, action) {
        const st = sealedState(ev);
        if (st === 'unsupported') return null;
        if (action === 'add' && st === 'sealed' && !tokens.get(ev.emoji)) {
            if (!isReactionToken(ev.emoji) || isSelf(ev.from_webid) || !ev.from_webid) return null;
            try {
                const body = await openSecondary(decrypt, ev.from_webid, ev, KIND_REACT);
                const emoji = checkReactionBody(body, ev);
                tokens.put(ev.emoji, { e: emoji, m: ev.message_id, own: false });
            } catch { return null; }
        }
        const emoji = translateReaction(ev.emoji);
        if (emoji == null) return null;
        if (action === 'remove' && emoji !== ev.emoji) tokens.remove(ev.emoji);
        const out = { ...ev, emoji, e2e: false };
        delete out.content;
        return out;
    }

    async function openFileMessage(ev) {
        const st = sealedState(ev);
        if (st === 'legacy') return ev;
        const failed = { ...ev, e2e: false, content: UNDECRYPTABLE, file: undefined, _sealedFailed: true };
        if (st === 'unsupported') return failed;
        if (isSelf(ev.from_webid)) {
            const mine = outbox.take(ev);
            return mine ? { ...ev, e2e: false, content: '📎 ' + mine.file.filename, file: mine.file } : null;
        }
        if (!ev.from_webid || !ev.file || typeof ev.file.data_b64 !== 'string') return failed;
        try {
            const meta = checkFileBody(await openSecondary(decrypt, ev.from_webid, ev, KIND_FILE));
            const u8 = await openFileBytes(b64ToU8(ev.file.data_b64), meta);
            const file = { filename: meta.filename, mime_type: meta.mime_type, size: u8.length, data_b64: u8ToB64(u8), spoiler: meta.spoiler };
            if (meta.width) { file.width = meta.width; file.height = meta.height; }
            return { ...ev, e2e: false, content: '📎 ' + meta.filename, file, _persistDm: true };
        } catch { return failed; }
    }

    // Open a sealed chunked-transfer offer. The result carries _sealedFile
    // (metadata and key) or _sealedBad (the transfer must be refused).
    async function openFileOffer(ev) {
        const st = sealedState(ev);
        if (st === 'legacy') return ev;
        if (st === 'unsupported' || !ev.from_webid) return { ...ev, _sealedBad: true };
        try {
            const meta = checkFileBody(await openSecondary(decrypt, ev.from_webid, ev, KIND_FILE));
            return { ...ev, filename: meta.filename, mime_type: meta.mime_type, _sealedFile: meta };
        } catch { return { ...ev, _sealedBad: true }; }
    }

    return {
        peerFor, mustSeal, sealEdit, sealReaction, reactionWireEmoji, sealFile,
        openEdit, openReaction, translateReaction, openFileMessage, openFileOffer,
        outbox,
    };
}
