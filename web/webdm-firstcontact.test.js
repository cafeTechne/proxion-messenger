/**
 * First-contact authentication on the gateway-free DM path (webdm.js).
 *
 * Runs the real ratchet (e2e.js) and real envelope signatures (dmsig.js) so the
 * assertions are about actual session state: an envelope whose sender is not
 * verified must never create a ratchet session for the WebID it claims.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const _ls = {};
globalThis.localStorage = {
    getItem:    k     => Object.prototype.hasOwnProperty.call(_ls, k) ? _ls[k] : null,
    setItem:    (k,v) => { _ls[k] = String(v); },
    removeItem: k     => { delete _ls[k]; },
    clear:      ()    => { for (const k in _ls) delete _ls[k]; },
};
vi.mock('./auth.js', () => ({
    solidSession: { info: { isLoggedIn: false } },
    podStorageRoot: () => null,
}));
vi.mock('./pod.js', () => ({ podPublishIdentityAcl: async () => true }));

import * as E2E from './e2e.js';
import { signDm, signFanout, verifyDmSig, verifyFanoutSig } from './dmsig.js';
import { createWebDm, createSenderVerifier, envelopeFromDmPayload } from './webdm.js';

const ALICE = 'https://alice.example/profile/card#me';
const BOB = 'https://bob.example/profile/card#me';

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
async function makeSigner() {
    const kp = await crypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify']);
    const raw = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));
    const mc = new Uint8Array(2 + raw.length); mc[0] = 0xed; mc[1] = 0x01; mc.set(raw, 2);
    return { priv: kp.privateKey, did: 'did:key:z' + b58encode(mc) };
}
async function makeX25519() {
    const kp = await crypto.subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
    const pub = await crypto.subtle.exportKey('jwk', kp.publicKey);
    const priv = await crypto.subtle.exportKey('jwk', kp.privateKey);
    return { privJwk: priv, pubB64u: pub.x };
}
async function useIdentity(id) {
    E2E._resetForTesting();
    localStorage.clear();
    localStorage.setItem('proxion_e2e_x25519_priv_jwk', JSON.stringify(id.privJwk));
    localStorage.setItem('proxion_e2e_x25519_pub_b64u', id.pubB64u);
    await E2E.initE2E();
}

// Alice encrypts `texts` to Bob as her first chain and returns signed envelopes.
// Encryption happens under Alice's identity; the caller then switches to Bob.
async function aliceEnvelopes(texts, { signer, aliceX, bobX, from = ALICE, msgIdPrefix = 'm' }) {
    await useIdentity(aliceX);
    E2E.cachePeerPub(BOB, bobX.pubB64u);
    const out = [];
    for (let i = 0; i < texts.length; i++) {
        const enc = await E2E.ratchetEncrypt(BOB, texts[i]);
        const env = envelopeFromDmPayload({
            message_id: msgIdPrefix + i, content: enc.ciphertext, e2e: true, nonce: enc.nonce,
            msg_num: enc.msgNum, pn: enc.pn, ratchet_pub: enc.ratchetPub, x25519_pub: aliceX.pubB64u,
        }, from, 'Alice');
        if (signer) Object.assign(env, await signDm(env, signer.priv, signer.did));
        out.push(env);
    }
    return out;
}

// What the recipient believes each WebID's published signer is (signer.json).
function verifierFor(published) {
    return async (env) => {
        if (!env || !env.signer || !env.sig) return false;
        const ok = env.kind === 'fanout' ? await verifyFanoutSig(env) : await verifyDmSig(env);
        if (!ok) return false;
        if (!(env.from_webid in published)) return 'unavailable';   // signer.json not reachable
        return published[env.from_webid] === env.signer;
    };
}

function bobInbox(drops, verifySender) {
    const events = [];
    const deleted = [];
    const notices = [];
    const pod = {
        podEnsureDmInbox: async () => 'https://bob.example/proxion/dm-inbox/',
        podDropDm: async () => true,
        podReadDmDrops: async () => drops.filter((d) => !deleted.includes(d.url)),
        podDeleteDmDrop: async (url) => { deleted.push(url); return true; },
    };
    const dm = createWebDm({
        pod,
        e2e: { cachePeerPub: E2E.cachePeerPub, ratchetDecrypt: E2E.ratchetDecrypt, hasSession: E2E.hasRatchetSession },
        notify: { watchResource: () => () => {} },
        handleEvent: (e) => events.push(e),
        getSelfWebId: () => BOB, getDisplayName: () => 'Bob', getMyDeviceId: () => 'bob-dev',
        verifySender,
        onUnverifiedDrop: (info) => notices.push(info),
    });
    return { dm, events, deleted, notices };
}

let aliceX, bobX, malloryX, aliceSigner, mallorySigner;
beforeEach(async () => {
    aliceX = await makeX25519();
    bobX = await makeX25519();
    malloryX = await makeX25519();
    aliceSigner = await makeSigner();
    mallorySigner = await makeSigner();
});

describe('gateway-free first contact', () => {
    it('an unsigned first-contact drop does not create a session', async () => {
        // Mallory encrypts to Bob's real key but claims to be Alice, unsigned.
        const [env] = await aliceEnvelopes(['hi from "alice"'], { signer: null, aliceX: malloryX, bobX });
        await useIdentity(bobX);
        const { dm, events } = bobInbox([{ url: 'd1', envelope: env }], verifierFor({ [ALICE]: aliceSigner.did }));
        await dm.drainOnce();
        expect(await E2E.loadRatchetState(ALICE)).toBe(null);
        expect(events.some((e) => e.content === 'hi from "alice"')).toBe(false);
        expect(E2E.isE2EEnabled(ALICE)).toBe(false);
    });

    it('a first-contact drop with a forged signature does not create a session', async () => {
        // Signed with Mallory's own key; Alice's published signer is different.
        const [env] = await aliceEnvelopes(['forged'], { signer: mallorySigner, aliceX: malloryX, bobX });
        await useIdentity(bobX);
        const { dm, events } = bobInbox([{ url: 'd1', envelope: env }], verifierFor({ [ALICE]: aliceSigner.did }));
        await dm.drainOnce();
        expect(await E2E.loadRatchetState(ALICE)).toBe(null);
        expect(events.some((e) => e.content === 'forged')).toBe(false);
    });

    it('a rejected first contact is deleted and surfaced as a notice, not opened', async () => {
        const [env] = await aliceEnvelopes(['forged'], { signer: mallorySigner, aliceX: malloryX, bobX });
        await useIdentity(bobX);
        const { dm, events, deleted, notices } = bobInbox([{ url: 'd1', envelope: env }], verifierFor({ [ALICE]: aliceSigner.did }));
        await dm.drainOnce();
        expect(deleted).toEqual(['d1']);
        expect(events).toHaveLength(0);
        expect(notices).toHaveLength(1);
        expect(notices[0]).toMatchObject({ from_webid: ALICE });
    });

    it('a first contact whose signer cannot be fetched yet is left for retry', async () => {
        const [env] = await aliceEnvelopes(['later'], { signer: aliceSigner, aliceX, bobX });
        await useIdentity(bobX);
        // Alice's signer.json is unreachable this pass.
        const { dm, events, deleted } = bobInbox([{ url: 'd1', envelope: env }], verifierFor({}));
        await dm.drainOnce();
        expect(await E2E.loadRatchetState(ALICE)).toBe(null);
        expect(events).toHaveLength(0);
        expect(deleted).toHaveLength(0);
    });

    it('a valid signed first contact still decrypts and creates the session', async () => {
        const [env] = await aliceEnvelopes(['hello bob'], { signer: aliceSigner, aliceX, bobX });
        await useIdentity(bobX);
        const { dm, events, deleted } = bobInbox([{ url: 'd1', envelope: env }], verifierFor({ [ALICE]: aliceSigner.did }));
        await dm.drainOnce();
        expect(events).toHaveLength(1);
        expect(events[0]).toMatchObject({ content: 'hello bob', sender_verified: true, from_webid: ALICE });
        expect(deleted).toEqual(['d1']);
        expect(await E2E.loadRatchetState(ALICE)).not.toBe(null);
        expect(E2E.isE2EEnabled(ALICE)).toBe(true);
    });

    it('a forged first contact does not block the real one that follows', async () => {
        const [forged] = await aliceEnvelopes(['forged'], { signer: mallorySigner, aliceX: malloryX, bobX, msgIdPrefix: 'f' });
        const [real] = await aliceEnvelopes(['real'], { signer: aliceSigner, aliceX, bobX });
        await useIdentity(bobX);
        const { dm, events } = bobInbox([{ url: 'f', envelope: forged }, { url: 'r', envelope: real }],
            verifierFor({ [ALICE]: aliceSigner.did }));
        await dm.drainOnce();
        expect(events.map((e) => e.content)).toEqual(['real']);
    });

    it('legit messages delivered out of order still decrypt', async () => {
        const envs = await aliceEnvelopes(['a', 'b', 'c', 'd'], { signer: aliceSigner, aliceX, bobX });
        await useIdentity(bobX);
        const order = [2, 0, 3, 1];
        const { dm, events } = bobInbox(order.map((i) => ({ url: 'd' + i, envelope: envs[i] })),
            verifierFor({ [ALICE]: aliceSigner.did }));
        await dm.drainOnce();
        expect(events.map((e) => e.content)).toEqual(['c', 'a', 'd', 'b']);
    });

    it('an established session keeps decrypting while the signer is temporarily unreachable', async () => {
        const envs = await aliceEnvelopes(['one', 'two', 'three'], { signer: aliceSigner, aliceX, bobX });
        await useIdentity(bobX);
        const first = bobInbox([{ url: 'd0', envelope: envs[0] }], verifierFor({ [ALICE]: aliceSigner.did }));
        await first.dm.drainOnce();
        expect(first.events[0].content).toBe('one');
        // Later messages arrive out of order while signer.json cannot be fetched.
        const later = bobInbox([{ url: 'd2', envelope: envs[2] }, { url: 'd1', envelope: envs[1] }], verifierFor({}));
        await later.dm.drainOnce();
        expect(later.events.map((e) => [e.content, e.sender_verified])).toEqual([['three', false], ['two', false]]);
    });
});

describe('gateway-free fan-out first contact', () => {
    async function fanoutEnvelope(signer) {
        await useIdentity(malloryX);
        E2E.cachePeerPub(BOB + '#bob-dev', bobX.pubB64u);
        const enc = await E2E.ratchetEncrypt(BOB + '#bob-dev', 'fan');
        const env = {
            v: 1, kind: 'fanout', from_webid: ALICE, message_id: 'fm', to_device_id: 'bob-dev',
            payload: { content: enc.ciphertext, e2e: true, nonce: enc.nonce, msg_num: enc.msgNum, pn: enc.pn,
                ratchet_pub: enc.ratchetPub, x25519_pub: malloryX.pubB64u, from_device_id: 'alice-dev' },
        };
        if (signer) Object.assign(env, await signFanout(env, signer.priv, signer.did));
        return env;
    }

    it('an unverified fan-out copy with no session is not dispatched for decryption', async () => {
        const env = await fanoutEnvelope(mallorySigner);
        await useIdentity(bobX);
        const { dm, events, deleted, notices } = bobInbox([{ url: 'f1', envelope: env }], verifierFor({ [ALICE]: aliceSigner.did }));
        await dm.drainOnce();
        expect(events).toHaveLength(0);
        expect(deleted).toEqual(['f1']);
        expect(notices).toHaveLength(1);
    });

    it('a verified fan-out copy is dispatched and may start a session', async () => {
        const env = await fanoutEnvelope(aliceSigner);
        await useIdentity(bobX);
        const { dm, events } = bobInbox([{ url: 'f1', envelope: env }], verifierFor({ [ALICE]: aliceSigner.did }));
        await dm.drainOnce();
        expect(events).toHaveLength(1);
        expect(events[0]).toMatchObject({ type: 'dm_fanout', sender_verified: true, allow_session_init: true });
    });
});

describe('ratchetDecrypt allowInit', () => {
    it('refuses to create a session when allowInit is false', async () => {
        const [env] = await aliceEnvelopes(['x'], { signer: null, aliceX, bobX });
        await useIdentity(bobX);
        await expect(E2E.ratchetDecrypt(ALICE, env.content, env.nonce, env.msg_num, env.ratchet_pub, env.pn, { allowInit: false }))
            .rejects.toBeInstanceOf(E2E.E2ENoSessionError);
        expect(await E2E.hasRatchetSession(ALICE)).toBe(false);
        // The error is still an E2EDecryptError for callers that only know that type.
        await expect(E2E.ratchetDecrypt(ALICE, env.content, env.nonce, env.msg_num, env.ratchet_pub, env.pn, { allowInit: false }))
            .rejects.toBeInstanceOf(E2E.E2EDecryptError);
    });
});

describe('createSenderVerifier', () => {
    const base = { from_webid: ALICE, message_id: 'm', content: 'C', e2e: true, nonce: 'N', msg_num: 0, pn: 0,
        ratchet_pub: 'R', x25519_pub: 'X', reply_to_id: null, from_display_name: 'Alice', timestamp: 't' };
    const make = (doc, verifyDeviceCert) => createSenderVerifier({
        verifyDmSig, verifyFanoutSig, fetchSigner: async () => doc, verifyDeviceCert,
    });

    it('rejects an unsigned envelope', async () => {
        expect(await make({ signer: aliceSigner.did })(base)).toBe('rejected');
    });

    it('rejects a bad signature', async () => {
        const env = { ...base, ...(await signDm(base, aliceSigner.priv, aliceSigner.did)), content: 'TAMPERED' };
        expect(await make({ signer: aliceSigner.did })(env)).toBe('rejected');
    });

    it('rejects a valid signature by a signer the sender did not publish', async () => {
        const env = { ...base, ...(await signDm(base, mallorySigner.priv, mallorySigner.did)) };
        expect(await make({ signer: aliceSigner.did })(env)).toBe('rejected');
    });

    it('reports unavailable when the signer identity cannot be fetched', async () => {
        const env = { ...base, ...(await signDm(base, aliceSigner.priv, aliceSigner.did)) };
        expect(await make(null)(env)).toBe('unavailable');
    });

    it('verifies the published signer', async () => {
        const env = { ...base, ...(await signDm(base, aliceSigner.priv, aliceSigner.did)) };
        expect(await make({ signer: aliceSigner.did })(env)).toBe('verified');
    });

    it('verifies a device certified by the published account', async () => {
        const env = { ...base, ...(await signDm(base, mallorySigner.priv, mallorySigner.did)), cert: { c: 1 } };
        const certOk = vi.fn(async () => 'did:key:zACCOUNT');
        expect(await make({ signer: aliceSigner.did, account_did: 'did:key:zACCOUNT' }, certOk)(env)).toBe('verified');
        expect(certOk).toHaveBeenCalledWith({ c: 1 }, { expectedAccountDid: 'did:key:zACCOUNT', expectedDeviceDid: mallorySigner.did });
    });
});
