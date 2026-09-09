// dmsig.js — sign & verify gateway-free DM envelopes (R107).
//
// A web-mode DM is a JSON envelope dropped into the recipient's public-Append pod
// inbox, so `from_webid` is only a claim. To let a recipient confirm a message
// really came from that WebID's owner, the sender signs the envelope with their
// Ed25519 identity key (a did:key), and the recipient verifies the signature
// against the signer identity the sender publishes at THEIR OWN pod — which an
// attacker forging a "from alice" message cannot write to. This module is the pure
// crypto: canonical bytes, sign, and signature-verify. The pod publish/fetch and
// the authorization decision (is this signer allowed to speak for that WebID?)
// live in the caller. No dependencies, so it is not swept up in module mocks.

const _ENC = new TextEncoder();
const _B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';

function _b58decode(str) {
    const map = {};
    for (let i = 0; i < _B58.length; i++) map[_B58[i]] = i;
    const bytes = [0];
    for (const ch of str) {
        const val = map[ch];
        if (val === undefined) throw new Error('bad base58 char');
        let carry = val;
        for (let j = 0; j < bytes.length; j++) {
            carry += bytes[j] * 58;
            bytes[j] = carry & 0xff;
            carry >>= 8;
        }
        while (carry > 0) { bytes.push(carry & 0xff); carry >>= 8; }
    }
    for (let k = 0; k < str.length && str[k] === '1'; k++) bytes.push(0);
    return new Uint8Array(bytes.reverse());
}

// Extract the raw 32-byte Ed25519 public key from a did:key (matches device-cert).
export function didToEd25519Pub(did) {
    if (typeof did !== 'string' || !did.startsWith('did:key:z')) throw new Error('not a did:key');
    const mc = _b58decode(did.slice('did:key:z'.length));
    if (mc.length < 34 || mc[0] !== 0xed || mc[1] !== 0x01) throw new Error('not an ed25519 did:key');
    return mc.slice(2, 34);
}

function _b64(bytes) { let s = ''; for (const b of bytes) s += String.fromCharCode(b); return btoa(s); }
function _b64dec(s) { const bin = atob(s); const u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return u; }

// The exact fields that are authenticated, in order. This binds the sender
// (from_webid), the message id, the ciphertext, and the ratchet/key material, so a
// tampered or replayed-under-a-different-claim envelope fails. The order and the
// length-prefixed framing must stay stable or signatures won't verify cross-client.
const SIGNED_FIELDS = ['from_webid', 'message_id', 'content', 'nonce', 'msg_num', 'pn', 'ratchet_pub', 'x25519_pub', 'e2e', 'reply_to_id', 'from_display_name', 'timestamp'];
// A multi-device fanout copy signs the same material plus its to_device_id (so a
// copy cannot be redirected to another device), reading the ciphertext/key fields
// from the nested payload rather than the envelope top level.
const SIGNED_FIELDS_FANOUT = ['from_webid', 'message_id', 'to_device_id', 'content', 'nonce', 'msg_num', 'pn', 'ratchet_pub', 'x25519_pub', 'e2e'];

// LEGACY field lists (pre-R110): the same order without e2e/reply_to_id/
// from_display_name. Kept only so the deprecation shim in verify can still accept a
// signature produced by an older client during rollout. Do not sign with these.
const SIGNED_FIELDS_LEGACY = ['from_webid', 'message_id', 'content', 'nonce', 'msg_num', 'pn', 'ratchet_pub', 'x25519_pub', 'timestamp'];
const SIGNED_FIELDS_FANOUT_LEGACY = ['from_webid', 'message_id', 'to_device_id', 'content', 'nonce', 'msg_num', 'pn', 'ratchet_pub', 'x25519_pub'];

// Canonical bytes: each field length-prefixed then joined by 0x7c. `prefixLen` is
// the width of the big-endian length header — 4 bytes in the current scheme (so a
// field ≥ 64KiB cannot truncate/collide), 2 in the legacy shim below.
function _canonicalP(fields, obj, prefixLen) {
    const parts = fields.map((k) => _ENC.encode(obj && obj[k] != null ? String(obj[k]) : ''));
    const chunks = parts.map((p) => {
        const c = new Uint8Array(prefixLen + p.length);
        for (let i = 0; i < prefixLen; i++) c[i] = (p.length >> (8 * (prefixLen - 1 - i))) & 0xff;
        c.set(p, prefixLen);
        return c;
    });
    const total = chunks.reduce((a, c) => a + c.length, 0) + (chunks.length - 1);
    const out = new Uint8Array(total);
    let off = 0;
    chunks.forEach((c, i) => { if (i > 0) out[off++] = 0x7c; out.set(c, off); off += c.length; });
    return out;
}

function _canonical(fields, obj) { return _canonicalP(fields, obj, 4); }
// A 2-byte length header can only frame a field under 64KiB; a longer field would
// wrap to its low 16 bits and make the legacy canonical bytes ambiguous (two
// different envelopes canonicalizing the same). No genuinely-old signature ever
// framed an oversized field, so refuse to build legacy bytes for one and let the
// caller treat it as unverified rather than risk a colliding canonical. Returns
// null when a field is too large; the 4-byte primary path has no such limit.
function _canonicalLegacy(fields, obj) {
    for (const k of fields) {
        if (_ENC.encode(obj && obj[k] != null ? String(obj[k]) : '').length >= 65536) return null;
    }
    return _canonicalP(fields, obj, 2);
}

function _fanoutObj(env) {
    const p = (env && env.payload) || {};
    return {
        from_webid: env && env.from_webid, message_id: env && env.message_id, to_device_id: env && env.to_device_id,
        content: p.content, nonce: p.nonce, msg_num: p.msg_num, pn: p.pn, ratchet_pub: p.ratchet_pub, x25519_pub: p.x25519_pub,
        e2e: p.e2e,
    };
}

export function canonicalDmBytes(env) { return _canonical(SIGNED_FIELDS, env); }

export function canonicalFanoutBytes(env) { return _canonical(SIGNED_FIELDS_FANOUT, _fanoutObj(env)); }

async function _sign(bytes, privKey, signerDid) {
    if (!privKey || !signerDid) return null;
    try {
        const sig = new Uint8Array(await crypto.subtle.sign('Ed25519', privKey, bytes));
        return { signer: signerDid, sig: _b64(sig) };
    } catch { return null; }
}
async function _verify(env, bytes) {
    try {
        if (!env || !env.signer || !env.sig) return false;
        const pub = await crypto.subtle.importKey('raw', didToEd25519Pub(env.signer), { name: 'Ed25519' }, false, ['verify']);
        return await crypto.subtle.verify('Ed25519', pub, _b64dec(env.sig), bytes);
    } catch { return false; }
}

// Sign an envelope; returns { signer, sig } to merge into it, or null if we cannot
// sign (no key). `privKey` is a non-extractable Ed25519 CryptoKey; `signerDid` is
// its did:key.
export function signDm(env, privKey, signerDid) { return _sign(canonicalDmBytes(env), privKey, signerDid); }
export function signFanout(env, privKey, signerDid) { return _sign(canonicalFanoutBytes(env), privKey, signerDid); }

// True iff env.sig is a valid signature by env.signer over the canonical bytes.
// Never throws. Does NOT decide authorization (whether env.signer may speak for
// env.from_webid) — the caller does that against the sender's published identity.
//
// Verify tries the CURRENT canonicalization first, then retries once with the
// LEGACY (2-byte-prefix, fewer fields) scheme so a message from an older client
// still verifies during rollout instead of flashing an "unverified" badge. Both are
// real Ed25519 signatures by the same signer; neither path is weakened.
export async function verifyDmSig(env) {
    if (await _verify(env, canonicalDmBytes(env))) return true;
    const legacy = _canonicalLegacy(SIGNED_FIELDS_LEGACY, env);
    return legacy ? _verify(env, legacy) : false;
}
export async function verifyFanoutSig(env) {
    if (await _verify(env, canonicalFanoutBytes(env))) return true;
    const legacy = _canonicalLegacy(SIGNED_FIELDS_FANOUT_LEGACY, _fanoutObj(env));
    return legacy ? _verify(env, legacy) : false;
}

// ── Long Chat room-message signing (#4) ──
// A SolidOS Long Chat message carries exactly one signature literal
// (sec:proofValue, per the Solid chat SHACL shape), so the proof has to be
// self-describing: it packs the signer's did:key and the base64 signature as
// "<did:key>|<b64sig>". The verifier splits on the FIRST '|' — a did:key holds
// only base58 and base64 has no '|', so the delimiter is unambiguous — recovers
// the public key from the did:key, and checks the signature. The signed material
// is framed with the same 4-byte length-prefixed scheme as a DM, so no field can
// be shifted into another and a tampered field fails to verify.

// Sign arbitrary canonical bytes into the compact "<did:key>|<b64sig>" proof
// string, or null if we have no key. Never throws.
async function _signProof(bytes, privKey, signerDid) {
    if (!privKey || !signerDid) return null;
    try {
        const sig = new Uint8Array(await crypto.subtle.sign('Ed25519', privKey, bytes));
        return `${signerDid}|${_b64(sig)}`;
    } catch { return null; }
}
// Verify a "<did:key>|<b64sig>" proof over `bytes`; returns the signer did:key on
// success (so the caller can authorize it against the actor's published identity),
// or null. Never throws. Does NOT decide authorization.
async function _verifyProof(bytes, proof) {
    try {
        if (typeof proof !== 'string') return null;
        const bar = proof.indexOf('|');
        if (bar <= 0) return null;
        const signer = proof.slice(0, bar);
        const sig = proof.slice(bar + 1);
        if (!sig) return null;
        const pub = await crypto.subtle.importKey('raw', didToEd25519Pub(signer), { name: 'Ed25519' }, false, ['verify']);
        return (await crypto.subtle.verify('Ed25519', pub, _b64dec(sig), bytes)) ? signer : null;
    } catch { return null; }
}

// The signed material now also covers foaf:maker's DISPLAY name (px:fromName, the
// `from_display_name` field), so a validly-signed message under an attacker's own
// maker cannot carry a spoofed name that reads as someone else. The LEGACY set is
// the pre-name shape (id, created, content, maker), kept only for the shim below.
const SIGNED_FIELDS_LONGCHAT = ['id', 'created', 'content', 'maker', 'from_display_name'];
const SIGNED_FIELDS_LONGCHAT_LEGACY = ['id', 'created', 'content', 'maker'];

export function canonicalLongChatBytes(msg) { return _canonical(SIGNED_FIELDS_LONGCHAT, msg); }

// Sign a room message; returns the compact "<did:key>|<b64sig>" proof string, or
// null if we have no key. `msg` carries { id, created, content, maker,
// from_display_name }.
export async function signLongChat(msg, privKey, signerDid) {
    return _signProof(canonicalLongChatBytes(msg), privKey, signerDid);
}

// Verify a proof string against the message's core fields. Returns the signer
// did:key on success (so the caller can authorize signer -> foaf:maker against the
// maker's published identity), or null. Never throws. Does NOT decide
// authorization — the caller does that against the maker's published signer.
//
// Tries the CURRENT field set (which covers from_display_name) first. Falls back
// to the LEGACY set (the four core fields) ONLY when no display name is present,
// so a message signed by the just-shipped code — which omitted from_display_name
// and never wrote px:fromName — still verifies during rollout. The fallback is
// deliberately skipped when a name IS present: a name has to be covered by the
// current-scheme signature, so an attacker cannot Append a px:fromName onto a
// legacy-signed message and have the legacy shim (which never signed a name) bless
// it. A spoofed name therefore fails verification and shows as unverified.
export async function verifyLongChatProof(msg, proof) {
    const signer = await _verifyProof(canonicalLongChatBytes(msg), proof);
    if (signer) return signer;
    if (msg && msg.from_display_name != null && msg.from_display_name !== '') return null;
    return _verifyProof(_canonical(SIGNED_FIELDS_LONGCHAT_LEGACY, msg), proof);
}

// ── Authenticated soft-delete (#5 Part A) ──
// A tombstone (schema:dateDeleted) under acl:Append can be written by ANY member,
// so on its own it lets a member censor anyone's message. The deleter therefore
// signs a proof over the message IRI and the deletion time; the reader honours a
// tombstone only when this proof verifies AND the signer is authorized (the
// message's foaf:maker for a self-delete, or the room owner for moderation).
const SIGNED_FIELDS_LONGCHAT_DELETE = ['iri', 'deletedIso'];
export function canonicalLongChatDeleteBytes(t) { return _canonical(SIGNED_FIELDS_LONGCHAT_DELETE, t); }
// `t` carries { iri, deletedIso }.
export async function signLongChatDelete(t, privKey, signerDid) {
    return _signProof(canonicalLongChatDeleteBytes(t), privKey, signerDid);
}
export async function verifyLongChatDeleteProof(t, proof) {
    return _verifyProof(canonicalLongChatDeleteBytes(t), proof);
}

// ── Authenticated reactions and un-reactions (#5 Part C) ──
// A schema:LikeAction and its append-only un-react tombstone are otherwise
// unauthenticated, so a member could forge a reaction as anyone or cancel
// anyone's. The reactor signs a proof over the action IRI, the target message IRI,
// the agent WebID and the emoji; only that agent may cancel, signing over the
// action IRI and the cancel time.
const SIGNED_FIELDS_LONGCHAT_REACTION = ['action', 'target', 'agent', 'emoji'];
export function canonicalLongChatReactionBytes(r) { return _canonical(SIGNED_FIELDS_LONGCHAT_REACTION, r); }
// `r` carries { action, target, agent, emoji }.
export async function signLongChatReaction(r, privKey, signerDid) {
    return _signProof(canonicalLongChatReactionBytes(r), privKey, signerDid);
}
export async function verifyLongChatReactionProof(r, proof) {
    return _verifyProof(canonicalLongChatReactionBytes(r), proof);
}

const SIGNED_FIELDS_LONGCHAT_CANCEL = ['action', 'canceledIso'];
export function canonicalLongChatCancelBytes(c) { return _canonical(SIGNED_FIELDS_LONGCHAT_CANCEL, c); }
// `c` carries { action, canceledIso }.
export async function signLongChatCancel(c, privKey, signerDid) {
    return _signProof(canonicalLongChatCancelBytes(c), privKey, signerDid);
}
export async function verifyLongChatCancelProof(c, proof) {
    return _verifyProof(canonicalLongChatCancelBytes(c), proof);
}
