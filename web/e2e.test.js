/**
 * web/e2e.test.js — Unit tests for the Phase 2 Double Ratchet E2E module.
 *
 * Runs in Node 20 (globalThis.crypto provides X25519 / AES-GCM / HKDF / HMAC).
 * localStorage is mocked below since the node environment doesn't provide it.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest';

// ── Mock localStorage ────────────────────────────────────────────────────────
const _ls = {};
globalThis.localStorage = {
    getItem:    k     => Object.prototype.hasOwnProperty.call(_ls, k) ? _ls[k] : null,
    setItem:    (k,v) => { _ls[k] = String(v); },
    removeItem: k     => { delete _ls[k]; },
    clear:      ()    => { for (const k in _ls) delete _ls[k]; },
};

// ── Mock auth.js (no pod in unit tests) ──────────────────────────────────────
vi.mock('./auth.js', () => ({
    solidSession: { info: { isLoggedIn: false } },
    podStorageRoot: () => null,
}));

// ── Import e2e module ────────────────────────────────────────────────────────
import {
    E2EDecryptError,
    initE2E,
    myX25519PubB64u,
    cachePeerPub,
    isE2EEnabled,
    ratchetEncrypt,
    ratchetDecrypt,
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
