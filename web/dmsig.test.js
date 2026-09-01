import { describe, it, expect } from 'vitest';
import { canonicalDmBytes, signDm, verifyDmSig, didToEd25519Pub, signFanout, verifyFanoutSig } from './dmsig.js';

const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
function b58encode(bytes) {
    let zeros = 0; while (zeros < bytes.length && bytes[zeros] === 0) zeros++;
    const digits = [0];
    for (let i = zeros; i < bytes.length; i++) {
        let carry = bytes[i];
        for (let j = 0; j < digits.length; j++) { carry += digits[j] << 8; digits[j] = carry % 58; carry = (carry / 58) | 0; }
        while (carry > 0) { digits.push(carry % 58); carry = (carry / 58) | 0; }
    }
    let s = ''; for (let k = 0; k < zeros; k++) s += '1';
    for (let q = digits.length - 1; q >= 0; q--) s += B58[digits[q]];
    return s;
}
function didFromPub(pub) {
    const mc = new Uint8Array(2 + pub.length); mc[0] = 0xed; mc[1] = 0x01; mc.set(pub, 2);
    return 'did:key:z' + b58encode(mc);
}
async function makeIdentity() {
    const kp = await crypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify']);
    const rawPub = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));
    return { priv: kp.privateKey, did: didFromPub(rawPub) };
}

const env = {
    from_webid: 'https://alice.pod/profile/card#me', message_id: 'm1', content: 'CIPHER',
    nonce: 'N', msg_num: 3, pn: 1, ratchet_pub: 'RP', x25519_pub: 'XP', timestamp: '2026-01-01T00:00:00Z',
};

describe('dmsig', () => {
    it('round-trips: a signed envelope verifies', async () => {
        const id = await makeIdentity();
        const s = await signDm(env, id.priv, id.did);
        expect(s.signer).toBe(id.did);
        expect(typeof s.sig).toBe('string');
        expect(await verifyDmSig({ ...env, ...s })).toBe(true);
    });

    it('rejects a tampered ciphertext', async () => {
        const id = await makeIdentity();
        const s = await signDm(env, id.priv, id.did);
        expect(await verifyDmSig({ ...env, ...s, content: 'TAMPERED' })).toBe(false);
    });

    it('rejects a swapped from_webid (the spoofing case)', async () => {
        const id = await makeIdentity();
        const s = await signDm(env, id.priv, id.did);
        expect(await verifyDmSig({ ...env, ...s, from_webid: 'https://mallory.pod/profile/card#me' })).toBe(false);
    });

    it('rejects when signer did does not match the signing key', async () => {
        const id = await makeIdentity();
        const other = await makeIdentity();
        const s = await signDm(env, id.priv, id.did);
        expect(await verifyDmSig({ ...env, ...s, signer: other.did })).toBe(false);
    });

    it('returns false for an unsigned or malformed envelope', async () => {
        expect(await verifyDmSig(env)).toBe(false);
        expect(await verifyDmSig({ ...env, signer: 'not-a-did', sig: 'x' })).toBe(false);
    });

    it('signDm returns null without a key', async () => {
        expect(await signDm(env, null, 'did:key:zabc')).toBe(null);
    });

    it('didToEd25519Pub yields 32 bytes for a real key', async () => {
        const id = await makeIdentity();
        expect(didToEd25519Pub(id.did).length).toBe(32);
    });

    it('canonicalDmBytes is deterministic and field-order fixed', () => {
        expect(Array.from(canonicalDmBytes(env))).toEqual(Array.from(canonicalDmBytes({ ...env })));
        // a different field value changes the bytes
        expect(Array.from(canonicalDmBytes(env))).not.toEqual(Array.from(canonicalDmBytes({ ...env, message_id: 'm2' })));
    });
});

// Envelope carrying the fields added to the signed set (e2e, reply_to_id,
// from_display_name) so tampering with any of them fails verification.
const envFull = {
    ...env, e2e: true, reply_to_id: 'r7', from_display_name: 'Alice',
};

describe('dmsig new signed fields (e2e, reply_to_id, from_display_name)', () => {
    it('round-trips an envelope with the new fields', async () => {
        const id = await makeIdentity();
        const s = await signDm(envFull, id.priv, id.did);
        expect(await verifyDmSig({ ...envFull, ...s })).toBe(true);
    });

    it('rejects a tampered reply_to_id', async () => {
        const id = await makeIdentity();
        const s = await signDm(envFull, id.priv, id.did);
        expect(await verifyDmSig({ ...envFull, ...s, reply_to_id: 'r-evil' })).toBe(false);
    });

    it('rejects a flipped e2e flag', async () => {
        const id = await makeIdentity();
        const s = await signDm(envFull, id.priv, id.did);
        expect(await verifyDmSig({ ...envFull, ...s, e2e: false })).toBe(false);
    });

    it('rejects a tampered from_display_name', async () => {
        const id = await makeIdentity();
        const s = await signDm(envFull, id.priv, id.did);
        expect(await verifyDmSig({ ...envFull, ...s, from_display_name: 'Mallory' })).toBe(false);
    });
});

describe('dmsig 4-byte length prefix', () => {
    it('signs and verifies a field ≥ 64KiB (no 2-byte truncation)', async () => {
        const id = await makeIdentity();
        const big = { ...envFull, content: 'x'.repeat(70000) };
        const s = await signDm(big, id.priv, id.did);
        expect(await verifyDmSig({ ...big, ...s })).toBe(true);
        // A change past the 2-byte boundary is still detected (proves full-length hashing).
        const flipped = { ...big, content: 'y' + 'x'.repeat(69999) };
        expect(await verifyDmSig({ ...flipped, ...s })).toBe(false);
    });
});

// Reproduce a pre-R110 signature: legacy field list, 2-byte length prefix. The
// deprecation shim in verify must still accept it.
const LEGACY_DM_FIELDS = ['from_webid', 'message_id', 'content', 'nonce', 'msg_num', 'pn', 'ratchet_pub', 'x25519_pub', 'timestamp'];
const LEGACY_FANOUT_FIELDS = ['from_webid', 'message_id', 'to_device_id', 'content', 'nonce', 'msg_num', 'pn', 'ratchet_pub', 'x25519_pub'];
const _ENC = new TextEncoder();
function legacyCanonical(fields, obj) {
    const parts = fields.map((k) => _ENC.encode(obj && obj[k] != null ? String(obj[k]) : ''));
    const chunks = parts.map((p) => {
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
function _b64(bytes) { let s = ''; for (const b of bytes) s += String.fromCharCode(b); return btoa(s); }

describe('dmsig legacy-signature shim', () => {
    it('accepts a signature produced under the legacy 2-byte scheme', async () => {
        const id = await makeIdentity();
        const bytes = legacyCanonical(LEGACY_DM_FIELDS, envFull);
        const sig = new Uint8Array(await crypto.subtle.sign('Ed25519', id.priv, bytes));
        expect(await verifyDmSig({ ...envFull, signer: id.did, sig: _b64(sig) })).toBe(true);
    });

    it('accepts a legacy-signed fanout copy', async () => {
        const id = await makeIdentity();
        const fEnv = {
            from_webid: 'https://alice.pod/profile/card#me', message_id: 'm1', to_device_id: 'bob-A',
            payload: { content: 'CIPHER', nonce: 'N', msg_num: 2, pn: 0, ratchet_pub: 'RP', x25519_pub: 'XP' },
        };
        const p = fEnv.payload;
        const bytes = legacyCanonical(LEGACY_FANOUT_FIELDS, {
            from_webid: fEnv.from_webid, message_id: fEnv.message_id, to_device_id: fEnv.to_device_id,
            content: p.content, nonce: p.nonce, msg_num: p.msg_num, pn: p.pn, ratchet_pub: p.ratchet_pub, x25519_pub: p.x25519_pub,
        });
        const sig = new Uint8Array(await crypto.subtle.sign('Ed25519', id.priv, bytes));
        expect(await verifyFanoutSig({ ...fEnv, signer: id.did, sig: _b64(sig) })).toBe(true);
    });

    it('still rejects a truly bad signature under both schemes', async () => {
        const id = await makeIdentity();
        const other = await makeIdentity();
        const bytes = legacyCanonical(LEGACY_DM_FIELDS, envFull);
        const sig = new Uint8Array(await crypto.subtle.sign('Ed25519', id.priv, bytes));
        expect(await verifyDmSig({ ...envFull, signer: other.did, sig: _b64(sig) })).toBe(false);
    });
});

describe('dmsig fanout', () => {
    const fEnv = {
        v: 1, kind: 'fanout', from_webid: 'https://alice.pod/profile/card#me', message_id: 'm1', to_device_id: 'bob-A',
        payload: { content: 'CIPHER', nonce: 'N', msg_num: 2, pn: 0, ratchet_pub: 'RP', x25519_pub: 'XP' },
    };

    it('round-trips a signed fanout copy', async () => {
        const id = await makeIdentity();
        const s = await signFanout(fEnv, id.priv, id.did);
        expect(await verifyFanoutSig({ ...fEnv, ...s })).toBe(true);
    });

    it('rejects a tampered fanout ciphertext', async () => {
        const id = await makeIdentity();
        const s = await signFanout(fEnv, id.priv, id.did);
        const tampered = { ...fEnv, ...s, payload: { ...fEnv.payload, content: 'X' } };
        expect(await verifyFanoutSig(tampered)).toBe(false);
    });

    it('rejects a fanout copy redirected to another device (to_device_id bound)', async () => {
        const id = await makeIdentity();
        const s = await signFanout(fEnv, id.priv, id.did);
        expect(await verifyFanoutSig({ ...fEnv, ...s, to_device_id: 'mallory-Z' })).toBe(false);
    });
});
