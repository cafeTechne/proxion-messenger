// callsec.js — end-to-end authentication for WebRTC calls (R79).
//
// 1:1 WebRTC media is already encrypted end to end by DTLS-SRTP: the peers derive
// the media keys during the DTLS handshake, so the gateway (which only relays the
// SDP/ICE signaling) and any TURN relay never see plaintext media. The one residual
// attack is a MALICIOUS gateway rewriting the DTLS fingerprint in the SDP it relays,
// to sit in the middle of the DTLS handshake.
//
// We close that: each peer signs the DTLS fingerprint from its own SDP with its
// Ed25519 identity key. The other peer checks the signature against the contact's
// KNOWN identity (their did:key) AND that the signed fingerprint equals the one in
// the SDP it actually received. A gateway cannot forge the contact's signature, so a
// swapped fingerprint is detected and the call is refused. Pure + no I/O; the crypto
// mirrors device-cert.js so it interops with the rest of the identity system.

const _ENC = new TextEncoder();
const DOMAIN = 'proxion-call-fingerprint-v1';
const _B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';

function _b58decode(str) {
    const map = {};
    for (let i = 0; i < _B58.length; i++) map[_B58[i]] = i;
    const bytes = [0];
    for (const ch of str) {
        const val = map[ch];
        if (val === undefined) throw new Error('bad base58 char');
        let carry = val;
        for (let j = 0; j < bytes.length; j++) { carry += bytes[j] * 58; bytes[j] = carry & 0xff; carry >>= 8; }
        while (carry > 0) { bytes.push(carry & 0xff); carry >>= 8; }
    }
    for (let k = 0; k < str.length && str[k] === '1'; k++) bytes.push(0);
    return new Uint8Array(bytes.reverse());
}

function _b58encode(bytes) {
    const digits = [0];
    for (const b of bytes) {
        let carry = b;
        for (let j = 0; j < digits.length; j++) { carry += digits[j] << 8; digits[j] = carry % 58; carry = (carry / 58) | 0; }
        while (carry > 0) { digits.push(carry % 58); carry = (carry / 58) | 0; }
    }
    let s = '';
    for (let k = 0; k < bytes.length && bytes[k] === 0; k++) s += '1';
    for (let i = digits.length - 1; i >= 0; i--) s += _B58[digits[i]];
    return s;
}

function _didToPubBytes(did) {
    if (typeof did !== 'string' || !did.startsWith('did:key:z')) throw new Error('not a did:key');
    const mc = _b58decode(did.slice('did:key:z'.length));
    if (mc.length < 34 || mc[0] !== 0xed || mc[1] !== 0x01) throw new Error('not an ed25519 did:key');
    return mc.slice(2, 34);
}

/** did:key for a raw 32-byte Ed25519 public key (multicodec 0xed01 + base58btc). */
export function ed25519PubToDid(pubBytes) {
    const mc = new Uint8Array(2 + pubBytes.length);
    mc[0] = 0xed; mc[1] = 0x01; mc.set(pubBytes, 2);
    return 'did:key:z' + _b58encode(mc);
}

function _b64std(bytes) { let s = ''; for (const b of bytes) s += String.fromCharCode(b); return btoa(s); }
function _b64dec(s) { const bin = atob(s); const u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return u; }

// Length-prefixed, domain-separated canonical bytes (same shape as device-cert).
function _canonical(parts) {
    const chunks = parts.map(s => {
        const p = _ENC.encode(String(s));
        const c = new Uint8Array(2 + p.length);
        c[0] = (p.length >> 8) & 0xff; c[1] = p.length & 0xff; c.set(p, 2);
        return c;
    });
    const total = chunks.reduce((a, c) => a + c.length, 0) + (chunks.length - 1);
    const out = new Uint8Array(total);
    let off = 0;
    chunks.forEach((c, i) => { if (i > 0) out[off++] = 0x7c; out.set(c, off); off += c.length; });
    return out;
}

// Every a=fingerprint line in an SDP, normalized. A single DTLS certificate yields
// one fingerprint shared by every m-line; more than one DISTINCT value is anomalous
// and treated as tampering during verification.
export function extractAllFingerprints(sdp) {
    if (typeof sdp !== 'string') return [];
    const out = [];
    const re = /a=fingerprint:(\S+)\s+([0-9A-Fa-f:]+)/ig;
    let m;
    while ((m = re.exec(sdp)) !== null) out.push(`${m[1].toLowerCase()} ${m[2].toUpperCase()}`);
    return out;
}

/**
 * The DTLS fingerprint from an SDP, normalized to "<hash-func> AA:BB:..." (algo
 * lower-case, hex upper-case), or null. A single DTLS certificate yields one
 * session fingerprint shared by every m-line, so the first is authoritative.
 */
export function extractFingerprint(sdp) {
    return extractAllFingerprints(sdp)[0] || null;
}

/** Sign a fingerprint bound to the session and role (offer/answer) with our identity key. */
export async function signFingerprint({ fingerprint, sessionId, role, privKey }) {
    const canon = _canonical([DOMAIN, fingerprint || '', sessionId || '', role || '']);
    const sig = new Uint8Array(await crypto.subtle.sign('Ed25519', privKey, canon));
    return _b64std(sig);
}

/** Verify a fingerprint signature against a signer's did:key. Never throws. */
export async function verifyFingerprint({ fingerprint, sessionId, role, signatureB64, signerDid }) {
    try {
        if (!fingerprint || !signatureB64 || !signerDid) return false;
        const pub = await crypto.subtle.importKey('raw', _didToPubBytes(signerDid), { name: 'Ed25519' }, false, ['verify']);
        return await crypto.subtle.verify('Ed25519', pub, _b64dec(signatureB64),
            _canonical([DOMAIN, fingerprint, sessionId || '', role || '']));
    } catch {
        return false;
    }
}

/**
 * Decide how much we trust a received SDP's media channel:
 *   'verified'     — signed by the expected contact and the fingerprint matches.
 *   'mismatch'     — a signature/fingerprint/identity that does NOT check out; a MitM
 *                    signal. The caller MUST refuse the call.
 *   'unverifiable' — no signature, no fingerprint, or the peer identity is unknown, so
 *                    we cannot prove it either way (allow, but surface as unverified).
 *
 * `expectedDid` is the contact's known identity (their did:key). When we know it, a
 * different signer or a bad signature is a 'mismatch', never merely 'unverifiable'.
 */
export async function classifyPeerSdp({ sdp, sessionId, role, signatureB64, signerDid, expectedDid }) {
    const fps = extractAllFingerprints(sdp);
    if (!fps.length) return 'unverifiable';
    // Divergent fingerprints across m-lines are anomalous: with one DTLS certificate
    // every line matches. More than one distinct value means the SDP was tampered to
    // redirect part of the media. For a known contact that is a mismatch; even for an
    // unknown peer we cannot soundly bind a signature to a split channel.
    const distinct = [...new Set(fps)];
    const fingerprint = fps[0];
    if (expectedDid) {
        // We know who this must be: anyone else, or no proof at all, is a failure.
        if (!signatureB64 || !signerDid) return 'mismatch';
        if (signerDid !== expectedDid) return 'mismatch';
        if (distinct.length !== 1) return 'mismatch';
        const ok = await verifyFingerprint({ fingerprint, sessionId, role, signatureB64, signerDid });
        return ok ? 'verified' : 'mismatch';
    }
    // Unknown peer identity: verify if we can, but we cannot bind it to a contact.
    if (signatureB64 && signerDid && distinct.length === 1) {
        const ok = await verifyFingerprint({ fingerprint, sessionId, role, signatureB64, signerDid });
        return ok ? 'verified' : 'mismatch';
    }
    return 'unverifiable';
}
