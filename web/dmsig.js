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
function _canonicalLegacy(fields, obj) { return _canonicalP(fields, obj, 2); }

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
    return _verify(env, _canonicalLegacy(SIGNED_FIELDS_LEGACY, env));
}
export async function verifyFanoutSig(env) {
    if (await _verify(env, canonicalFanoutBytes(env))) return true;
    return _verify(env, _canonicalLegacy(SIGNED_FIELDS_FANOUT_LEGACY, _fanoutObj(env)));
}
