// device-cert.js — account device certificates for multi-device linking.
//
// Mirrors proxion_messenger_core/device_cert.py BYTE-FOR-BYTE so a certificate
// issued here (the primary device signs with its non-extractable clientDid key
// via WebCrypto) verifies on the Python gateway, and vice versa.
//
// Delegation, not key-copying: the account's private key only signs; it never
// leaves the primary device (device identity keys are non-extractable, R9.1).
//
// Canonical signing bytes (identical to the Python _canonical):
//   for each of [account_did, device_did, str(issued), str(expires)]:
//     2-byte big-endian length + utf8(part)
//   joined by a single 0x7C ("|") byte between the length-prefixed chunks.

const _ENC = new TextEncoder();
const MAX_TTL_DAYS = 400;
const _B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

function _b58decode(str) {
    const map = {};
    for (let i = 0; i < _B58.length; i++) map[_B58[i]] = i;
    const bytes = [0];
    for (const ch of str) {
        const val = map[ch];
        if (val === undefined) throw new Error("bad base58 char");
        let carry = val;
        for (let j = 0; j < bytes.length; j++) {
            carry += bytes[j] * 58;
            bytes[j] = carry & 0xff;
            carry >>= 8;
        }
        while (carry > 0) { bytes.push(carry & 0xff); carry >>= 8; }
    }
    // leading '1's are leading zero bytes
    for (let k = 0; k < str.length && str[k] === "1"; k++) bytes.push(0);
    return new Uint8Array(bytes.reverse());
}

function _didToPubBytes(did) {
    if (!did.startsWith("did:key:z")) throw new Error("not a did:key");
    const mc = _b58decode(did.slice("did:key:z".length));
    if (mc.length < 34 || mc[0] !== 0xed || mc[1] !== 0x01) throw new Error("not an ed25519 did:key");
    return mc.slice(2, 34);
}

function _canonical(accountDid, deviceDid, issuedAt, expiresAt) {
    const parts = [accountDid, deviceDid, String(issuedAt), String(expiresAt)].map(s => _ENC.encode(s));
    const chunks = parts.map(p => {
        const c = new Uint8Array(2 + p.length);
        c[0] = (p.length >> 8) & 0xff;
        c[1] = p.length & 0xff;
        c.set(p, 2);
        return c;
    });
    const total = chunks.reduce((a, c) => a + c.length, 0) + (chunks.length - 1);
    const out = new Uint8Array(total);
    let off = 0;
    chunks.forEach((c, i) => {
        if (i > 0) out[off++] = 0x7c; // "|"
        out.set(c, off);
        off += c.length;
    });
    return out;
}

function _b64std(bytes) {
    let s = "";
    for (const b of bytes) s += String.fromCharCode(b);
    return btoa(s);
}

function _b64dec(s) {
    const bin = atob(s);
    const u = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    return u;
}

// Issue a certificate authorizing `deviceDid` to act for the account.
// `accountPrivKey` is a WebCrypto Ed25519 private CryptoKey (the primary's
// identity key); `accountDid` is its did:key (must correspond, or it won't
// verify). Returns a plain object ready to JSON-serialize.
export async function issueDeviceCert(accountPrivKey, accountDid, deviceDid, opts = {}) {
    const ttlDays = opts.ttlDays ?? 365;
    if (ttlDays <= 0 || ttlDays > MAX_TTL_DAYS) throw new Error(`ttlDays must be 1..${MAX_TTL_DAYS}`);
    if (!deviceDid.startsWith("did:key:")) throw new Error("device_did must be a did:key");
    const issued = Math.floor(opts.now == null ? Date.now() / 1000 : opts.now);
    const expires = issued + ttlDays * 86400;
    const canon = _canonical(accountDid, deviceDid, issued, expires);
    const sig = new Uint8Array(await crypto.subtle.sign("Ed25519", accountPrivKey, canon));
    return {
        "@type": "ProxionDeviceCert",
        version: 1,
        account_did: accountDid,
        device_did: deviceDid,
        issued_at: issued,
        expires_at: expires,
        signature: _b64std(sig),
    };
}

// Verify a certificate. Returns the authorized account_did on success, else null.
// Never throws. `expectedDeviceDid` should be this device's own DID so a cert
// meant for another device is not mistaken as ours.
export async function verifyDeviceCert(cert, opts = {}) {
    try {
        if (!cert || typeof cert !== "object") return null;
        const accountDid = cert.account_did || "";
        const deviceDid = cert.device_did || "";
        const signature = cert.signature || "";
        const issued = parseInt(cert.issued_at, 10);
        const expires = parseInt(cert.expires_at, 10);
        if (!accountDid || !deviceDid || !signature) return null;
        if (!accountDid.startsWith("did:key:") || !deviceDid.startsWith("did:key:")) return null;
        if (opts.expectedDeviceDid && deviceDid !== opts.expectedDeviceDid) return null;
        if (opts.expectedAccountDid && accountDid !== opts.expectedAccountDid) return null;
        if (!Number.isFinite(issued) || !Number.isFinite(expires)) return null;
        const nowI = Math.floor(opts.now == null ? Date.now() / 1000 : opts.now);
        if (expires <= issued || expires <= nowI) return null;
        if (expires - issued > MAX_TTL_DAYS * 86400) return null;
        const pub = await crypto.subtle.importKey(
            "raw", _didToPubBytes(accountDid), { name: "Ed25519" }, false, ["verify"]);
        const ok = await crypto.subtle.verify(
            "Ed25519", pub, _b64dec(signature), _canonical(accountDid, deviceDid, issued, expires));
        return ok ? accountDid : null;
    } catch {
        return null;
    }
}

// Exposed for tests / debugging — the exact bytes that get signed.
export const _internals = { _canonical, _didToPubBytes, _b58decode };
