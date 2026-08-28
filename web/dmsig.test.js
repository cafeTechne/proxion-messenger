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
