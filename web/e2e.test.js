/**
 * web/e2e.test.js — Unit tests for the Phase 2 Double Ratchet E2E module.
 *
 * Runs in Node 20 (globalThis.crypto provides X25519 / AES-GCM / HKDF / HMAC).
 * localStorage is mocked below since the node environment doesn't provide it.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { readFileSync } from 'node:fs';
import { dmHistorySave, dmHistorySetEnabled } from './dmhistory.js';
import { createWebDm } from './webdm.js';
import { createGatewayDmReceiver, gatewayDmEnvelope } from './gateway-dm-receive.js';
import { createDmOutbox } from './dm-outbox.js';
import { createDmReceiptStore, dmEnvelopeFingerprint } from './dm-receipts.js';

// ── Mock localStorage ────────────────────────────────────────────────────────
const _ls = {};
globalThis.localStorage = {
    getItem:    k     => Object.prototype.hasOwnProperty.call(_ls, k) ? _ls[k] : null,
    setItem:    (k,v) => { _ls[k] = String(v); },
    removeItem: k     => { delete _ls[k]; },
    clear:      ()    => { for (const k in _ls) delete _ls[k]; },
    key: i => Object.keys(_ls)[i] ?? null,
    get length() { return Object.keys(_ls).length; },
};

// ── Mock auth.js (no pod in unit tests) ──────────────────────────────────────
// Mutable so a test can flip to a logged-in pod session; defaults to logged-out so
// the many initE2E() calls below leave the pod publish path a no-op.
let _e2eSession = { info: { isLoggedIn: false } };
let _e2eRoot = null;
vi.mock('./auth.js', () => ({
    get solidSession() { return _e2eSession; },
    podStorageRoot: () => _e2eRoot,
}));

// e2e.js publishes the x25519 public key + its public-read ACL through pod.js; spy
// on the ACL helper so a test can assert the trust anchor is made peer-readable.
const _publishIdentityAcl = vi.fn(async () => true);
vi.mock('./pod.js', () => ({
    podPublishIdentityAcl: (...a) => _publishIdentityAcl(...a),
}));

// ── Import e2e module ────────────────────────────────────────────────────────
import {
    E2EDecryptError,
    initE2E,
    myX25519PubB64u,
    cachePeerPub,
    onPeerPubChanged,
    isE2EEnabled,
    isPeerVerified,
    markPeerVerified,
    ratchetEncrypt,
    ratchetDecrypt,
    finishDmReceive,
    pendingDmReceives,
    finishDmSend, saveDmSendJob, listDmSendJobs, removeDmSendJob,
    loadRatchetState,
    saveRatchetState,
    advanceChain,
    deriveRootAndChain,
    kdfRk,
    safetyNumber,
    hkdf,
    b64uEnc,
    b64uDec,
    MAX_SKIP,
    _resetForTesting,
} from './e2e.js';

// ── Helpers ───────────────────────────────────────────────────────────────────

async function makeX25519Pair() {
    const kp = await crypto.subtle.generateKey(
        { name: 'X25519' }, true, ['deriveBits']);
    const pubJwk  = await crypto.subtle.exportKey('jwk', kp.publicKey);
    const privJwk = await crypto.subtle.exportKey('jwk', kp.privateKey);
    return { privateKey: kp.privateKey, privJwk, pubB64u: pubJwk.x };
}

// Swap the module's identity to a given keypair.
async function swapIdentity(privJwk, pubB64u) {
    _resetForTesting();
    localStorage.setItem('proxion_e2e_x25519_priv_jwk', JSON.stringify(privJwk));
    localStorage.setItem('proxion_e2e_x25519_pub_b64u', pubB64u);
    await initE2E();
}

// ── Crypto primitive tests ────────────────────────────────────────────────────

describe('crypto primitives', () => {
    it('advanceChain is deterministic', async () => {
        const key = crypto.getRandomValues(new Uint8Array(32));
        const { msgKey: m1, nextKey: n1 } = await advanceChain(key);
        const { msgKey: m2, nextKey: n2 } = await advanceChain(key);
        expect(Array.from(m1)).toEqual(Array.from(m2));
        expect(Array.from(n1)).toEqual(Array.from(n2));
    });

    it('advanceChain msgKey ≠ nextKey ≠ input', async () => {
        const key = crypto.getRandomValues(new Uint8Array(32));
        const { msgKey, nextKey } = await advanceChain(key);
        expect(Array.from(msgKey)).not.toEqual(Array.from(nextKey));
        expect(Array.from(msgKey)).not.toEqual(Array.from(key));
        expect(Array.from(nextKey)).not.toEqual(Array.from(key));
    });

    it('deriveRootAndChain is deterministic', async () => {
        const dh = crypto.getRandomValues(new Uint8Array(32));
        const { rootKey: r1, chainKey: c1 } = await deriveRootAndChain(dh);
        const { rootKey: r2, chainKey: c2 } = await deriveRootAndChain(dh);
        expect(Array.from(r1)).toEqual(Array.from(r2));
        expect(Array.from(c1)).toEqual(Array.from(c2));
    });

    it('kdfRk is deterministic and produces 64 bytes split as 32+32', async () => {
        const root  = crypto.getRandomValues(new Uint8Array(32));
        const dhBits = crypto.getRandomValues(new Uint8Array(32));
        const { newRootKey: r1, chainKey: c1 } = await kdfRk(root, dhBits);
        const { newRootKey: r2, chainKey: c2 } = await kdfRk(root, dhBits);
        expect(Array.from(r1)).toEqual(Array.from(r2));
        expect(Array.from(c1)).toEqual(Array.from(c2));
        expect(r1).toHaveLength(32);
        expect(c1).toHaveLength(32);
    });

    it('kdfRk output changes with different rootKey (root used as HKDF salt)', async () => {
        const dh  = crypto.getRandomValues(new Uint8Array(32));
        const { newRootKey: r1 } = await kdfRk(new Uint8Array(32), dh);
        const { newRootKey: r2 } = await kdfRk(new Uint8Array(32).fill(1), dh);
        expect(Array.from(r1)).not.toEqual(Array.from(r2));
    });

    it('DH is symmetric: DH(a_priv, b_pub) === DH(b_priv, a_pub)', async () => {
        const alice = await makeX25519Pair();
        const bob   = await makeX25519Pair();

        const bobPubKey = await crypto.subtle.importKey(
            'raw', b64uDec(bob.pubB64u), { name: 'X25519' }, false, []);
        const dhAlice = new Uint8Array(await crypto.subtle.deriveBits(
            { name: 'X25519', public: bobPubKey }, alice.privateKey, 256));

        const alicePubKey = await crypto.subtle.importKey(
            'raw', b64uDec(alice.pubB64u), { name: 'X25519' }, false, []);
        const dhBob = new Uint8Array(await crypto.subtle.deriveBits(
            { name: 'X25519', public: alicePubKey }, bob.privateKey, 256));

        expect(Array.from(dhAlice)).toEqual(Array.from(dhBob));
    });

    it('b64uEnc / b64uDec roundtrip', () => {
        const bytes = crypto.getRandomValues(new Uint8Array(33)); // non-multiple of 3
        expect(Array.from(b64uDec(b64uEnc(bytes)))).toEqual(Array.from(bytes));
    });

    it('MAX_SKIP is 20', () => {
        expect(MAX_SKIP).toBe(20);
    });
});

// ── safetyNumber ──────────────────────────────────────────────────────────────

describe('safetyNumber', () => {
    it('is deterministic', async () => {
        const a = b64uEnc(crypto.getRandomValues(new Uint8Array(32)));
        const b = b64uEnc(crypto.getRandomValues(new Uint8Array(32)));
        expect(await safetyNumber(a, b)).toBe(await safetyNumber(a, b));
    });

    it('is symmetric: safetyNumber(a,b) === safetyNumber(b,a)', async () => {
        const a = b64uEnc(crypto.getRandomValues(new Uint8Array(32)));
        const b = b64uEnc(crypto.getRandomValues(new Uint8Array(32)));
        expect(await safetyNumber(a, b)).toBe(await safetyNumber(b, a));
    });

    it('output matches "ddddd ddddd ddddd ddddd ddddd ddddd" format', async () => {
        const a = b64uEnc(new Uint8Array(32));
        const b = b64uEnc(new Uint8Array(32).fill(1));
        const sn = await safetyNumber(a, b);
        expect(sn).toMatch(/^\d{5}( \d{5}){5}$/);
    });

    it('different inputs produce different safety numbers', async () => {
        const a = b64uEnc(new Uint8Array(32));
        const b = b64uEnc(new Uint8Array(32).fill(1));
        const c = b64uEnc(new Uint8Array(32).fill(2));
        expect(await safetyNumber(a, b)).not.toBe(await safetyNumber(a, c));
    });
});

// ── Module init ───────────────────────────────────────────────────────────────

describe('onPeerPubChanged', () => {
    it('fires when a peer key is first learned or changes, not on a repeat', () => {
        const seen = [];
        onPeerPubChanged((id) => seen.push(id));
        cachePeerPub('notify-peer', 'keyA');
        cachePeerPub('notify-peer', 'keyA');
        cachePeerPub('notify-peer', 'keyB');
        expect(seen).toEqual(['notify-peer', 'notify-peer']);
    });
});

describe('contact verification', () => {
    it('verifies the current key, and a changed key reads unverified again', () => {
        expect(isPeerVerified('v-peer')).toBe(false);
        expect(markPeerVerified('v-peer')).toBe(false);      // no key to verify yet
        cachePeerPub('v-peer', 'keyA');
        expect(markPeerVerified('v-peer')).toBe(true);
        expect(isPeerVerified('v-peer')).toBe(true);
        cachePeerPub('v-peer', 'keyB');                       // their key changed
        expect(isPeerVerified('v-peer')).toBe(false);
    });
});

describe('initE2E', () => {
    beforeEach(() => {
        localStorage.clear();
        _resetForTesting();
    });

    it('sets e2eSupported=true and generates keypair', async () => {
        await initE2E();
        const mod = await import('./e2e.js');
        expect(mod.e2eSupported).toBe(true);
        expect(myX25519PubB64u()).toBeTruthy();
        expect(localStorage.getItem('proxion_e2e_x25519_pub_b64u')).toBeTruthy();
        expect(localStorage.getItem('proxion_e2e_x25519_priv_jwk')).toBeTruthy();
    });

    it('reloads persisted keypair', async () => {
        await initE2E();
        const pub1 = myX25519PubB64u();
        _resetForTesting();
        await initE2E();
        expect(myX25519PubB64u()).toBe(pub1);
    });
});

// ── Alice → Bob Phase 2 roundtrip ─────────────────────────────────────────────

describe('Alice→Bob ratchet (Phase 2)', () => {
    let alice, bob;

    beforeAll(async () => {
        localStorage.clear();
        _resetForTesting();
        await initE2E();
        alice = {
            pubB64u: myX25519PubB64u(),
            privJwk: JSON.parse(localStorage.getItem('proxion_e2e_x25519_priv_jwk')),
        };
        bob = await makeX25519Pair();
    });

    it('ratchetEncrypt returns ratchetPub (not keyHeader) on every message', async () => {
        cachePeerPub('bob-phase2', bob.pubB64u);
        const r0 = await ratchetEncrypt('bob-phase2', 'first');
        const r1 = await ratchetEncrypt('bob-phase2', 'second');
        expect(r0.ratchetPub).toBeTruthy();
        expect(r1.ratchetPub).toBeTruthy();
        // Same send chain → same ratchetPub until recv triggers DH step
        expect(r0.ratchetPub).toBe(r1.ratchetPub);
        expect(r0.msgNum).toBe(0);
        expect(r1.msgNum).toBe(1);
    });

    it('serializes concurrent ratchet ops on the same peer (unique msgNum, no race)', async () => {
        cachePeerPub('bob-concurrent', bob.pubB64u);
        // Fire several encrypts at once. Without per-peer serialization they would
        // read the same sendMsgNum and collide; serialized they are 0..4.
        const results = await Promise.all([0, 1, 2, 3, 4].map(() => ratchetEncrypt('bob-concurrent', 'm')));
        const nums = results.map((r) => r.msgNum).sort((a, b) => a - b);
        expect(nums).toEqual([0, 1, 2, 3, 4]);
    });

    it('Bob decrypts Alice first message via ratchetDecrypt', async () => {
        cachePeerPub('bob-rt', bob.pubB64u);
        const enc = await ratchetEncrypt('bob-rt', 'hello bob');

        await swapIdentity(bob.privJwk, bob.pubB64u);

        const plain = await ratchetDecrypt(
            'alice-rt', enc.ciphertext, enc.nonce, enc.msgNum, enc.ratchetPub, enc.pn);
        expect(plain).toBe('hello bob');

        // Restore Alice
        await swapIdentity(alice.privJwk, alice.pubB64u);
    });

    it('Bob decrypts two sequential messages', async () => {
        cachePeerPub('bob-seq', bob.pubB64u);
        const e0 = await ratchetEncrypt('bob-seq', 'msg0');
        const e1 = await ratchetEncrypt('bob-seq', 'msg1');

        await swapIdentity(bob.privJwk, bob.pubB64u);

        const p0 = await ratchetDecrypt('alice-seq', e0.ciphertext, e0.nonce, e0.msgNum, e0.ratchetPub, e0.pn);
        const p1 = await ratchetDecrypt('alice-seq', e1.ciphertext, e1.nonce, e1.msgNum, e1.ratchetPub, e1.pn);
        expect(p0).toBe('msg0');
        expect(p1).toBe('msg1');

        await swapIdentity(alice.privJwk, alice.pubB64u);
    });
});

// ── Multi-round DH ratchet ────────────────────────────────────────────────────

describe('multi-round DH ratchet', () => {
    it('ratchetPub rotates on each direction switch', async () => {
        localStorage.clear();
        _resetForTesting();
        await initE2E();

        const alice = {
            pubB64u: myX25519PubB64u(),
            privJwk: JSON.parse(localStorage.getItem('proxion_e2e_x25519_priv_jwk')),
        };
        const bob = await makeX25519Pair();

        // === Round 1: Alice sends 2 msgs to Bob ===
        cachePeerPub('bob-mr', bob.pubB64u);
        const a0 = await ratchetEncrypt('bob-mr', 'a0');
        const a1 = await ratchetEncrypt('bob-mr', 'a1');
        const aliceRatchetPub0 = a0.ratchetPub;
        expect(a1.ratchetPub).toBe(aliceRatchetPub0); // no recv yet → same key

        // === Bob receives and replies ===
        await swapIdentity(bob.privJwk, bob.pubB64u);

        const da0 = await ratchetDecrypt('alice-mr', a0.ciphertext, a0.nonce, a0.msgNum, a0.ratchetPub, a0.pn);
        const da1 = await ratchetDecrypt('alice-mr', a1.ciphertext, a1.nonce, a1.msgNum, a1.ratchetPub, a1.pn);
        expect(da0).toBe('a0');
        expect(da1).toBe('a1');

        cachePeerPub('alice-mr', alice.pubB64u);
        const b0 = await ratchetEncrypt('alice-mr', 'b0'); // Bob's first reply
        const bobRatchetPub = b0.ratchetPub;
        expect(bobRatchetPub).not.toBe(aliceRatchetPub0); // Bob has a different key

        // === Alice receives Bob's reply — DH step on recv → pendingDhRatchet=true ===
        await swapIdentity(alice.privJwk, alice.pubB64u);

        const db0 = await ratchetDecrypt('bob-mr', b0.ciphertext, b0.nonce, b0.msgNum, b0.ratchetPub, b0.pn);
        expect(db0).toBe('b0');

        // Alice sends next msg — pendingDhRatchet=true → DH step on send → new ratchetPub
        const a2 = await ratchetEncrypt('bob-mr', 'a2');
        expect(a2.ratchetPub).not.toBe(aliceRatchetPub0); // rotated
        expect(a2.ratchetPub).not.toBe(bobRatchetPub);    // Alice's fresh key, not Bob's
        expect(a2.msgNum).toBe(0);                         // new send chain

        // === Bob receives Alice's new-chain message ===
        await swapIdentity(bob.privJwk, bob.pubB64u);

        const da2 = await ratchetDecrypt('alice-mr', a2.ciphertext, a2.nonce, a2.msgNum, a2.ratchetPub, a2.pn);
        expect(da2).toBe('a2');
    });
});

// ── Within-chain out-of-order delivery ───────────────────────────────────────

describe('within-chain out-of-order decrypt', () => {
    it('delivers msgs 0,1,2 out-of-order via skip buffer', async () => {
        localStorage.clear();
        _resetForTesting();
        await initE2E();

        const alice = {
            pubB64u: myX25519PubB64u(),
            privJwk: JSON.parse(localStorage.getItem('proxion_e2e_x25519_priv_jwk')),
        };
        const bob = await makeX25519Pair();

        cachePeerPub('bob-oo', bob.pubB64u);
        const e0 = await ratchetEncrypt('bob-oo', 'A');
        const e1 = await ratchetEncrypt('bob-oo', 'B');
        const e2 = await ratchetEncrypt('bob-oo', 'C');

        await swapIdentity(bob.privJwk, bob.pubB64u);

        // Deliver out-of-order: C (2), A (0), B (1)
        const pC = await ratchetDecrypt('alice-oo', e2.ciphertext, e2.nonce, 2, e2.ratchetPub, e2.pn);
        expect(pC).toBe('C');
        const pA = await ratchetDecrypt('alice-oo', e0.ciphertext, e0.nonce, 0, e0.ratchetPub, e0.pn);
        expect(pA).toBe('A');
        const pB = await ratchetDecrypt('alice-oo', e1.ciphertext, e1.nonce, 1, e1.ratchetPub, e1.pn);
        expect(pB).toBe('B');
    });
});

// ── Cross-chain out-of-order (skip buffer survives DH ratchet step) ──────────

describe('cross-chain out-of-order decrypt', () => {
    it('delivers msgs from old chain after new ratchet pub received', async () => {
        localStorage.clear();
        _resetForTesting();
        await initE2E();

        const alice = {
            pubB64u: myX25519PubB64u(),
            privJwk: JSON.parse(localStorage.getItem('proxion_e2e_x25519_priv_jwk')),
        };
        const bob = await makeX25519Pair();

        // === Alice sends 3 messages on chain-0 ===
        cachePeerPub('bob-xchain', bob.pubB64u);
        const a0 = await ratchetEncrypt('bob-xchain', 'chain0-msg0');
        const a1 = await ratchetEncrypt('bob-xchain', 'chain0-msg1');
        const a2 = await ratchetEncrypt('bob-xchain', 'chain0-msg2');
        const aliceRatchetPub0 = a0.ratchetPub;

        // === Bob receives a0, replies (triggers new recv chain on Alice side) ===
        await swapIdentity(bob.privJwk, bob.pubB64u);
        await ratchetDecrypt('alice-xchain', a0.ciphertext, a0.nonce, a0.msgNum, a0.ratchetPub, a0.pn);

        cachePeerPub('alice-xchain', alice.pubB64u);
        const b0 = await ratchetEncrypt('alice-xchain', 'bob-reply');

        // === Alice receives Bob's reply → DH ratchet step on recv (pendingDhRatchet=true) ===
        await swapIdentity(alice.privJwk, alice.pubB64u);
        await ratchetDecrypt('bob-xchain', b0.ciphertext, b0.nonce, b0.msgNum, b0.ratchetPub, b0.pn);

        // === Alice sends on NEW chain-1 (DH ratchet step on send) ===
        const a3 = await ratchetEncrypt('bob-xchain', 'chain1-msg0');
        expect(a3.ratchetPub).not.toBe(aliceRatchetPub0); // fresh ratchet key
        expect(a3.pn).toBe(3); // Alice sent 3 msgs on chain-0

        // === Bob now receives chain-1 msg first, then chain-0 stragglers ===
        await swapIdentity(bob.privJwk, bob.pubB64u);

        // Deliver chain-1 msg (triggers DH ratchet step on Bob; buffers chain-0 gaps with pn=3)
        const p3 = await ratchetDecrypt('alice-xchain', a3.ciphertext, a3.nonce, a3.msgNum, a3.ratchetPub, a3.pn);
        expect(p3).toBe('chain1-msg0');

        // Deliver chain-0 msg1 and msg2 (out-of-order from old chain, in skippedKeys)
        const p1 = await ratchetDecrypt('alice-xchain', a1.ciphertext, a1.nonce, a1.msgNum, a1.ratchetPub, a1.pn);
        expect(p1).toBe('chain0-msg1');
        const p2 = await ratchetDecrypt('alice-xchain', a2.ciphertext, a2.nonce, a2.msgNum, a2.ratchetPub, a2.pn);
        expect(p2).toBe('chain0-msg2');
    });
});

// ── Skip buffer overflow ──────────────────────────────────────────────────────

describe('skip buffer overflow', () => {
    let alice, bob;
    let e0; // Alice's first message (used to establish Bob's recv state)

    beforeAll(async () => {
        localStorage.clear();
        _resetForTesting();
        await initE2E();
        alice = {
            pubB64u: myX25519PubB64u(),
            privJwk: JSON.parse(localStorage.getItem('proxion_e2e_x25519_priv_jwk')),
        };
        bob = await makeX25519Pair();
        cachePeerPub('bob-overflow', bob.pubB64u);
        e0 = await ratchetEncrypt('bob-overflow', 'seed'); // establishes Alice's ratchetPub
    });

    it('fails when skipping more than MAX_SKIP messages', async () => {
        await swapIdentity(bob.privJwk, bob.pubB64u);

        // Decrypt the seed message to initialize Bob's recv state
        await ratchetDecrypt('alice-overflow', e0.ciphertext, e0.nonce, e0.msgNum, e0.ratchetPub, e0.pn);

        // Try to decrypt a message at msgNum=MAX_SKIP+1 (needs to skip MAX_SKIP msgs)
        const dummyCt    = b64uEnc(crypto.getRandomValues(new Uint8Array(48)));
        const dummyNonce = b64uEnc(crypto.getRandomValues(new Uint8Array(12)));

        await expect(
            ratchetDecrypt('alice-overflow', dummyCt, dummyNonce, MAX_SKIP + 1, e0.ratchetPub, 0)
        ).rejects.toThrow(E2EDecryptError);

        await swapIdentity(alice.privJwk, alice.pubB64u);
    });

    it('succeeds at exactly MAX_SKIP messages skipped', async () => {
        // Alice encrypts MAX_SKIP+1 messages (msgs 0..MAX_SKIP)
        cachePeerPub('bob-boundary', bob.pubB64u);
        const msgs = [];
        for (let i = 0; i <= MAX_SKIP; i++) {
            msgs.push(await ratchetEncrypt('bob-boundary', `m${i}`));
        }

        await swapIdentity(bob.privJwk, bob.pubB64u);

        // Decrypt the last one (skips MAX_SKIP msgs → exactly at the limit)
        const last = msgs[MAX_SKIP];
        const plain = await ratchetDecrypt(
            'alice-boundary', last.ciphertext, last.nonce, last.msgNum, last.ratchetPub, last.pn);
        expect(plain).toBe(`m${MAX_SKIP}`);

        await swapIdentity(alice.privJwk, alice.pubB64u);
    });
});

// ── Forged / hostile ciphertext must not corrupt a live session ──────────────

describe('hostile-message hardening', () => {
    let alice, bob;

    beforeAll(async () => {
        localStorage.clear();
        _resetForTesting();
        await initE2E();
        alice = {
            pubB64u: myX25519PubB64u(),
            privJwk: JSON.parse(localStorage.getItem('proxion_e2e_x25519_priv_jwk')),
        };
        bob = await makeX25519Pair();
    });

    it('a forged ciphertext (new ratchet_pub, bad tag) does not brick the session', async () => {
        cachePeerPub('bob-forge', bob.pubB64u);
        const m0 = await ratchetEncrypt('bob-forge', 'real-0');
        const m1 = await ratchetEncrypt('bob-forge', 'real-1');

        await swapIdentity(bob.privJwk, bob.pubB64u);

        // Bob decrypts the genuine first message → establishes recv state.
        expect(await ratchetDecrypt('alice-forge', m0.ciphertext, m0.nonce, m0.msgNum, m0.ratchetPub, m0.pn))
            .toBe('real-0');

        // Attacker injects a message with a *new* ratchet_pub (forces a DH-ratchet
        // step) and a garbage body. It must throw AND leave the session intact.
        const forgedPub = (await makeX25519Pair()).pubB64u;
        const junkCt    = b64uEnc(crypto.getRandomValues(new Uint8Array(48)));
        const junkNonce = b64uEnc(crypto.getRandomValues(new Uint8Array(12)));
        await expect(
            ratchetDecrypt('alice-forge', junkCt, junkNonce, 0, forgedPub, 0)
        ).rejects.toThrow(E2EDecryptError);

        // The genuine follow-up (same original chain) must still decrypt.
        expect(await ratchetDecrypt('alice-forge', m1.ciphertext, m1.nonce, m1.msgNum, m1.ratchetPub, m1.pn))
            .toBe('real-1');

        await swapIdentity(alice.privJwk, alice.pubB64u);
    });

    it('a forged oversized pn is bounded (no unbounded skip loop)', async () => {
        cachePeerPub('bob-pn', bob.pubB64u);
        const m0 = await ratchetEncrypt('bob-pn', 'seed');

        await swapIdentity(bob.privJwk, bob.pubB64u);
        await ratchetDecrypt('alice-pn', m0.ciphertext, m0.nonce, m0.msgNum, m0.ratchetPub, m0.pn);

        // New ratchet_pub + an absurd pn: the DH-ratchet skip loop must give up at
        // MAX_SKIP rather than iterating pn times.
        const forgedPub = (await makeX25519Pair()).pubB64u;
        const junkCt    = b64uEnc(crypto.getRandomValues(new Uint8Array(48)));
        const junkNonce = b64uEnc(crypto.getRandomValues(new Uint8Array(12)));
        await expect(
            ratchetDecrypt('alice-pn', junkCt, junkNonce, 0, forgedPub, 1_000_000)
        ).rejects.toThrow(E2EDecryptError);

        await swapIdentity(alice.privJwk, alice.pubB64u);
    });
});

// ── Phase 1 state migration ───────────────────────────────────────────────────

describe('Phase 1 state migration', () => {
    it('Phase 1 state (no rootKey) is discarded → clean re-init on next encrypt', async () => {
        localStorage.clear();
        _resetForTesting();
        await initE2E();

        const bob = await makeX25519Pair();
        cachePeerPub('bob-migration', bob.pubB64u);

        // Save a fake Phase 1 state (no rootKey field)
        await saveRatchetState('bob-migration', {
            sendChain: Array.from(crypto.getRandomValues(new Uint8Array(32))),
            recvChain: null,
            sendMsgNum: 5,
            recvMsgNum: 0,
            skippedRecv: {},
            lastKeyHeader: 'oldphase1header',
        });

        // Next encrypt should discard Phase 1 state and start fresh (msgNum=0)
        const enc = await ratchetEncrypt('bob-migration', 'after migration');
        expect(enc.msgNum).toBe(0);       // fresh session
        expect(enc.ratchetPub).toBeTruthy(); // Phase 2 ratchetPub present
    });
});

// ── State persistence ─────────────────────────────────────────────────────────

describe('state persistence', () => {
    it('state survives localStorage round-trip', async () => {
        localStorage.clear();
        _resetForTesting();
        await initE2E();

        const bob = await makeX25519Pair();
        cachePeerPub('bob-persist', bob.pubB64u);

        // Establish state
        await ratchetEncrypt('bob-persist', 'msg0');

        // Wipe in-memory cache; keep localStorage
        _resetForTesting();
        await initE2E(); // reloads same keypair
        cachePeerPub('bob-persist', bob.pubB64u);

        // Next encrypt should continue from msgNum=1 (state was persisted)
        const r2 = await ratchetEncrypt('bob-persist', 'msg1');
        expect(r2.msgNum).toBe(1);
        expect(r2.ratchetPub).toBeTruthy();
    });
});

describe('cachePeerPub verified-flag pinning', () => {
    it('clears the verified mark when a peer key changes, keeps it when unchanged', () => {
        localStorage.clear();
        cachePeerPub('alice', 'KEY_A');
        localStorage.setItem('proxion_e2e_verified_alice', '1');
        cachePeerPub('alice', 'KEY_A');   // same key → verification stands
        expect(localStorage.getItem('proxion_e2e_verified_alice')).toBe('1');
        cachePeerPub('alice', 'KEY_B');   // key changed → verification cleared
        expect(localStorage.getItem('proxion_e2e_verified_alice')).toBe(null);
        expect(localStorage.getItem('proxion_e2e_peer_pub_alice')).toBe('KEY_B');
    });
});

describe('publishing the x25519 public key makes it peer-readable (D2)', () => {
    // Let init's fire-and-forget _publishPubToPod() settle.
    const flush = () => new Promise((r) => setTimeout(r, 0));

    it('writes x25519-pub.json then a public-read ACL on that file', async () => {
        const calls = [];
        _e2eRoot = 'https://me.pod.example/';
        _e2eSession = {
            info: { isLoggedIn: true, webId: 'https://me.pod.example/profile/card#me' },
            fetch: vi.fn(async (url, opts = {}) => { calls.push({ url, opts }); return { ok: true, status: 200 }; }),
        };
        _publishIdentityAcl.mockClear();
        try {
            localStorage.clear();
            _resetForTesting();
            await initE2E();
            await flush();
            const pubUrl = 'https://me.pod.example/proxion/identity/x25519-pub.json';
            const put = calls.find((c) => c.url === pubUrl && c.opts.method === 'PUT');
            expect(put).toBeTruthy();
            expect(JSON.parse(put.opts.body)).toMatchObject({ version: 1 });
            // The public-read ACL is applied to exactly that file (not the container).
            expect(_publishIdentityAcl).toHaveBeenCalledWith(pubUrl);
        } finally {
            _e2eSession = { info: { isLoggedIn: false } };
            _e2eRoot = null;
            _resetForTesting();
        }
    });

    it('does not touch the pod (or its ACL) when logged out', async () => {
        _publishIdentityAcl.mockClear();
        localStorage.clear();
        _resetForTesting();
        await initE2E();
        await flush();
        expect(_publishIdentityAcl).not.toHaveBeenCalled();
    });
});


describe('recoverable pod DM receives', () => {
    let bob, encrypted;
    const receiveId = 'a'.repeat(64);
    beforeEach(async () => {
        _e2eSession = { info: { isLoggedIn: false } }; _e2eRoot = null;
        localStorage.clear();
        const alice = await makeX25519Pair(); bob = await makeX25519Pair();
        await swapIdentity(alice.privJwk, alice.pubB64u);
        cachePeerPub('recovery-bob', bob.pubB64u);
        encrypted = await ratchetEncrypt('recovery-bob', 'recover this message');
        await swapIdentity(bob.privJwk, bob.pubB64u);
    });
    const decrypt = (enc, opts = {}) => ratchetDecrypt('recovery-alice', enc.ciphertext,
        enc.nonce, enc.msgNum, enc.ratchetPub, enc.pn, opts);

    it('recovers across reload without advancing the ratchet again, then erases recovery', async () => {
        expect(await decrypt(encrypted, { receiveId })).toBe('recover this message');
        const version = (await loadRatchetState('recovery-alice')).ver;
        expect(localStorage.getItem('proxion_e2e_state_recovery-alice')).not.toContain('recover this message');
        _resetForTesting(); await initE2E();
        expect(await decrypt(encrypted, { receiveId })).toBe('recover this message');
        expect((await loadRatchetState('recovery-alice')).ver).toBe(version);
        await finishDmReceive('recovery-alice', receiveId);
        _resetForTesting(); await initE2E();
        await expect(decrypt(encrypted)).rejects.toThrow('already consumed');
        expect((await loadRatchetState('recovery-alice')).pendingReceives).toEqual({});
    });

    it('leaves the key retryable when the atomic local write fails', async () => {
        const set = vi.spyOn(localStorage, 'setItem').mockImplementationOnce(() => { throw Error('quota'); });
        await expect(decrypt(encrypted, { receiveId })).rejects.toThrow();
        set.mockRestore();
        expect(await loadRatchetState('recovery-alice')).toBeNull();
        expect(await decrypt(encrypted, { receiveId })).toBe('recover this message');
    });

    it('does not commit after the receiving account changes during decryption', async () => {
        let active = true;
        const subtle = crypto.subtle;
        const original = subtle.decrypt.bind(subtle);
        const spy = vi.spyOn(subtle, 'decrypt').mockImplementation(async (...args) => {
            const result = await original(...args); active = false; return result;
        });
        await expect(decrypt(encrypted, { receiveId, current: () => active })).rejects.toThrow();
        spy.mockRestore();
        expect(await loadRatchetState('recovery-alice')).toBeNull();
        expect(await decrypt(encrypted, { receiveId })).toBe('recover this message');
    });

    it('applies backpressure instead of evicting an undelivered recovery copy', async () => {
        await decrypt(encrypted, { receiveId });
        const state = await loadRatchetState('recovery-alice');
        state.pendingReceives = Object.fromEntries(Array.from({ length: 64 }, (_, i) => [i.toString(16).padStart(64, '0'), 'pending']));
        await saveRatchetState('recovery-alice', state);
        await expect(decrypt(encrypted, { receiveId: 'b'.repeat(64) })).rejects.toThrow('recovery is full');
        expect(Object.keys((await loadRatchetState('recovery-alice')).pendingReceives)).toHaveLength(64);
    });

    function reader(receipts, persistMessage, drops, events, deleteDrop = async () => true) {
        return createWebDm({
            pod: { podReadDmDrops: async () => drops, podDeleteDmDrop: deleteDrop },
            e2e: { ratchetDecrypt, finishDmReceive, cachePeerPub: vi.fn() }, receipts,
            getSelfWebId: () => 'https://bob.example/profile/card#me', getMyDeviceId: () => 'bob-device',
            verifySender: async () => 'verified', persistMessage,
            handleEvent: msg => events.push(msg),
        });
    }
    const drop = enc => ({ url: 'https://bob.example/proxion/dm-inbox/recovery.json', envelope: {
        from_webid: 'recovery-alice', message_id: 'recovery-message', content: enc.ciphertext,
        e2e: true, nonce: enc.nonce, msg_num: enc.msgNum, ratchet_pub: enc.ratchetPub, pn: enc.pn,
    } });

    it.each(['same reader', 'reload'])('retries failed history through %s before deleting the drop', async mode => {
        const db = new IDBFactory(), receipts = createDmReceiptStore(db);
        const d = drop(encrypted), events = [], deleted = vi.fn(async () => true);
        const persist = vi.fn().mockResolvedValueOnce(false).mockResolvedValue(true);
        let dm = reader(receipts, persist, [d], events, deleted);
        await dm.drainOnce();
        expect(events).toHaveLength(1); expect(deleted).not.toHaveBeenCalled();
        if (mode === 'reload') {
            _resetForTesting(); await initE2E();
            dm = reader(createDmReceiptStore(db), persist, [d], events, deleted);
        }
        await dm.drainOnce();
        expect(persist).toHaveBeenCalledTimes(2);
        expect(persist.mock.calls[1][0].content).toBe('recover this message');
        expect(events).toHaveLength(mode === 'reload' ? 2 : 1);
        expect(deleted).toHaveBeenCalledTimes(1);
        expect((await loadRatchetState('recovery-alice')).pendingReceives).toEqual({});
        // A copied envelope at a different URL is acknowledged using its receipt.
        const copy = { ...d, url: d.url.replace('recovery.json', 'duplicate.json') };
        await reader(createDmReceiptStore(db), persist, [copy], events, deleted).drainOnce();
        expect(persist).toHaveBeenCalledTimes(2);
        expect(deleted).toHaveBeenCalledTimes(2);
    });

    it('retries receipt storage after a reload without losing the already saved history', async () => {
        const db = new IDBFactory(), receipts = createDmReceiptStore(db);
        const remember = vi.spyOn(receipts, 'remember').mockRejectedValueOnce(Error('storage unavailable'));
        const d = drop(encrypted), events = [], deleted = vi.fn(async () => true), history = new Map();
        const persist = async msg => { history.set(msg.message_id, msg); return true; };
        await reader(receipts, persist, [d], events, deleted).drainOnce();
        expect(deleted).not.toHaveBeenCalled(); expect(history.size).toBe(1);
        _resetForTesting(); await initE2E();
        remember.mockRestore();
        await reader(createDmReceiptStore(db), persist, [d], [], deleted).drainOnce();
        expect(deleted).toHaveBeenCalledTimes(1); expect(history.size).toBe(1);
        expect(history.get('recovery-message').content).toBe('recover this message');
    });

    it('honors disabled history and clears the temporary recovery copy', async () => {
        dmHistorySetEnabled(false);
        try {
            const deleted = vi.fn(async () => true);
            await reader(createDmReceiptStore(new IDBFactory()), dmHistorySave, [drop(encrypted)], [], deleted).drainOnce();
            expect(deleted).toHaveBeenCalledTimes(1);
            expect((await loadRatchetState('recovery-alice')).pendingReceives).toEqual({});
        } finally { dmHistorySetEnabled(true); }
    });

    it('recovers actual fanout dispatch across failed history and reload', async () => {
        const source = readFileSync(new URL('./main.js', import.meta.url), 'utf8');
        const declaration = source.slice(source.indexOf('async function _handleEventAsync('), source.indexOf('// True for a DM thread'));
        const db = new IDBFactory(), receipts = createDmReceiptStore(db), events = [];
        const persist = vi.fn().mockResolvedValueOnce(false).mockResolvedValue(true);
        const deps = {
            crossMode: null, crossRooms: null, clientDid: 'bob-device', selfWebId: 'bob',
            cachePeerPub: vi.fn(), ratchetDecrypt, E2EDecryptError,
            isSealedPlain: () => false, isUndecryptable: text => text.startsWith('[could not decrypt]') || text.startsWith('[decryption error]'),
            peerDidToCertId: {}, handleEvent: msg => events.push(msg), dmHistorySave: persist,
        };
        const dispatch = new Function(...Object.keys(deps), declaration + '; return _handleEventAsync;')(...Object.values(deps));
        const d = { ...drop(encrypted), envelope: { kind: 'fanout', from_webid: 'recovery-alice',
            message_id: 'fanout-recovery', to_device_id: 'bob-device', payload: {
                content: encrypted.ciphertext, nonce: encrypted.nonce, msg_num: encrypted.msgNum,
                ratchet_pub: encrypted.ratchetPub, pn: encrypted.pn, e2e: true,
            } } };
        const deleted = vi.fn(async () => true);
        const options = { pod: { podReadDmDrops: async () => [d], podDeleteDmDrop: deleted },
            e2e: { ratchetDecrypt, finishDmReceive }, receipts,
            getSelfWebId: () => 'bob', getMyDeviceId: () => 'bob-device', verifySender: async () => 'verified',
            handleEvent: msg => dispatch(msg, true),
        };
        let dm = createWebDm(options);
        await dm.drainOnce();
        expect(events).toHaveLength(1); expect(deleted).not.toHaveBeenCalled();
        // Same-reader retries persist again without re-rendering.
        persist.mockResolvedValueOnce(false);
        await dm.drainOnce();
        expect(events).toHaveLength(1); expect(deleted).not.toHaveBeenCalled();
        _resetForTesting(); await initE2E();
        dm = createWebDm({ ...options, receipts: createDmReceiptStore(db) });
        await dm.drainOnce();
        expect(deleted).toHaveBeenCalledTimes(1);
        expect(events.at(-1).content).toBe('recover this message');
        expect((await loadRatchetState('recovery-alice#recovery-alice')).pendingReceives).toEqual({});
    });

    it.each(['message', 'dm_fanout'])('restores a gateway %s after reload without receiving its socket event again', async type => {
        const db = new IDBFactory(), events = [], history = new Map();
        let storageWorks = false;
        const persist = async message => {
            if (!storageWorks) return false;
            history.set(message.message_id, message); return true;
        };
        const payload = { content: encrypted.ciphertext, nonce: encrypted.nonce, msg_num: encrypted.msgNum,
            ratchet_pub: encrypted.ratchetPub, pn: encrypted.pn, e2e: true };
        const event = { type, from_webid: 'recovery-alice', message_id: 'gateway-recovery',
            ...(type === 'message' ? payload : { payload, to_device_id: 'bob-device' }) };
        const options = {
            e2e: { ratchetDecrypt, finishDmReceive, pendingDmReceives }, receipts: createDmReceiptStore(db),
            persistMessage: persist, handleEvent: message => events.push(message), getScope: () => 'gateway-bob-device',
            toMessage: raw => ({ type: 'message', message_id: raw.message_id, thread_id: 'alice-thread', from_webid: raw.from_webid }),
        };
        let receiver = createGatewayDmReceiver(options);
        expect(await receiver.receive(event)).toBe(false);
        expect(events).toHaveLength(1);
        expect(await pendingDmReceives()).toHaveLength(1);
        receiver.stop(); _resetForTesting(); await initE2E();
        storageWorks = true;
        receiver = createGatewayDmReceiver({ ...options, receipts: createDmReceiptStore(db) });
        await receiver.recover();
        expect(history.get('gateway-recovery').content).toBe('recover this message');
        expect(await pendingDmReceives()).toHaveLength(0);
        expect(events).toHaveLength(2); // restored reader renders its one recovered copy
        expect(await receiver.receive(event)).toBe(true);
        expect(events).toHaveLength(2);
        const fingerprint = await dmEnvelopeFingerprint(gatewayDmEnvelope(event));
        expect(await options.receipts.has('gateway-bob-device', fingerprint)).toBe(true);
    });

    it('resumes after the receipt commits but recovery cleanup is interrupted', async () => {
        const db = new IDBFactory(), receipts = createDmReceiptStore(db);
        const d = drop(encrypted), fingerprint = await dmEnvelopeFingerprint(d.envelope);
        await decrypt(encrypted, { receiveId: fingerprint });
        const scope = JSON.stringify(['https://bob.example/profile/card#me', 'bob-device']);
        await receipts.remember(scope, fingerprint, d.url);
        _resetForTesting(); await initE2E();
        const persist = vi.fn(), events = [], deleted = vi.fn(async () => true);
        await reader(receipts, persist, [d], events, deleted).drainOnce();
        expect(events).toHaveLength(0); expect(persist).not.toHaveBeenCalled();
        expect(deleted).toHaveBeenCalledTimes(1);
        expect((await loadRatchetState('recovery-alice')).pendingReceives).toEqual({});
    });
});


describe('atomic encrypted sender outbox', () => {
    let bob;
    beforeEach(async () => {
        _e2eSession = { info: { isLoggedIn: false } }; _e2eRoot = null;
        localStorage.clear(); _resetForTesting(); await initE2E();
        bob = await makeX25519Pair(); cachePeerPub('atomic-bob', bob.pubB64u);
    });
    const input = () => ({ id: 'atomic-send', actor: 'alice', device: 'alice-device', peer: 'bob-account',
        pub: myX25519PubB64u(), mode: 'single', entries: [{ peerId: 'atomic-bob', account: 'bob-account', primary: true }],
        route: { cmd: 'local_dm', target_webid: 'bob-account' },
        record: { message_id: 'atomic-send', thread_id: 'bob-thread', from_webid: 'alice', content: 'durable hello',
            from_display_name: 'Alice', timestamp: '2026-01-01T00:00:00.000Z' } });
    function harness() {
        const frames = [];
        const options = { e2e: { ratchetEncrypt, finishDmSend, saveDmSendJob, listDmSendJobs, removeDmSendJob },
            getScope: () => 'alice-device', send: async frame => { frames.push(frame); },
            persistMessage: async () => true, clearDelivery: async () => true, onError: vi.fn() };
        return { frames, options, engine: () => createDmOutbox(options) };
    }
    it('does not spend a key when the first intent commit fails', async () => {
        const h = harness(), engine = h.engine(), original = localStorage.setItem;
        const spy = vi.spyOn(localStorage, 'setItem').mockImplementation((key, value) => {
            if (key.startsWith('proxion_e2e_send_job_')) throw Error('intent quota');
            return original(key, value);
        });
        try { await expect(engine.queue(input())).rejects.toThrow('intent quota'); } finally { spy.mockRestore(); }
        expect(await loadRatchetState('atomic-bob')).toBeNull(); expect(h.frames).toHaveLength(0);
    });
    it('recovers exact ciphertext when prepared-job persistence fails after ratchet advancement', async () => {
        const h = harness(), engine = h.engine(); await engine.queue(input());
        const original = localStorage.setItem;
        const spy = vi.spyOn(localStorage, 'setItem').mockImplementation((key, value) => {
            if (key.startsWith('proxion_e2e_send_job_')) throw Error('prepared quota');
            return original(key, value);
        });
        expect(await engine.send('atomic-send')).toBe(false); spy.mockRestore();
        expect(h.frames).toHaveLength(0);
        const spent = await loadRatchetState('atomic-bob');
        const [exact] = Object.values(spent.pendingSends);
        expect(spent.sendMsgNum).toBe(1); expect((await listDmSendJobs())[0].frames).toBeUndefined();
        expect(localStorage.getItem('proxion_e2e_send_job_atomic-send')).not.toContain('durable hello');
        _resetForTesting(); await initE2E();
        await h.engine().restore({ force: true });
        expect(h.frames[0]).toMatchObject({ content: exact.ciphertext, nonce: exact.nonce, msg_num: exact.msgNum });
        expect((await loadRatchetState('atomic-bob')).sendMsgNum).toBe(1);
        await swapIdentity(bob.privJwk, bob.pubB64u);
        expect(await ratchetDecrypt('atomic-alice', h.frames[0].content, h.frames[0].nonce,
            h.frames[0].msg_num, h.frames[0].ratchet_pub, h.frames[0].pn)).toBe('durable hello');
    });
    it('rolls back the live sender on a failed state commit and retries the same message number', async () => {
        await ratchetEncrypt('atomic-bob', 'earlier message');
        const h = harness(), engine = h.engine(); await engine.queue(input());
        const original = localStorage.setItem;
        const spy = vi.spyOn(localStorage, 'setItem').mockImplementation((key, value) => {
            if (key === 'proxion_e2e_state_atomic-bob') throw Error('state quota');
            return original(key, value);
        });
        expect(await engine.send('atomic-send')).toBe(false); spy.mockRestore();
        expect((await loadRatchetState('atomic-bob')).sendMsgNum).toBe(1);
        await engine.send('atomic-send');
        expect(h.frames[0].msg_num).toBe(1); expect((await loadRatchetState('atomic-bob')).sendMsgNum).toBe(2);
    });
    it('recovers a partly prepared device fanout without advancing the first device twice', async () => {
        const h = harness(), engine = h.engine(), job = input(); job.mode = 'fanout';
        job.entries = [ { peerId: 'bob-device-1', account: 'bob-account', device: 'b1', pub: bob.pubB64u },
            { peerId: 'bob-device-2', account: 'bob-account', device: 'b2', pub: (await makeX25519Pair()).pubB64u } ];
        await engine.queue(job);
        const original = localStorage.setItem;
        const spy = vi.spyOn(localStorage, 'setItem').mockImplementation((key, value) => {
            if (key === 'proxion_e2e_state_bob-device-2') throw Error('second device quota');
            return original(key, value);
        });
        expect(await engine.send('atomic-send')).toBe(false); spy.mockRestore(); expect(h.frames).toHaveLength(0);
        const [firstCipher] = Object.values((await loadRatchetState('bob-device-1')).pendingSends);
        _resetForTesting(); await initE2E();
        const restored = h.engine(); await restored.restore({ force: true });
        expect(h.frames[0].fanout[0].payload.content).toBe(firstCipher.ciphertext);
        expect((await loadRatchetState('bob-device-1')).sendMsgNum).toBe(1);
        expect((await loadRatchetState('bob-device-2')).sendMsgNum).toBe(1);
        await restored.confirm(job.id, { kind: 'fanout', delivered: [{ to_webid: 'bob-account', to_device_id: 'b1' },
            { to_webid: 'bob-account', to_device_id: 'b2' }] });
        expect(await listDmSendJobs()).toEqual([]);
        expect((await loadRatchetState('bob-device-1')).pendingSends).toEqual({});
    });
});
