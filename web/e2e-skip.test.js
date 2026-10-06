/**
 * Skipped-message-key buffer: bounded work per message, bounded storage, and no
 * permanent failure after messages are lost for good.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const _ls = {};
globalThis.localStorage = {
    getItem:    k     => Object.prototype.hasOwnProperty.call(_ls, k) ? _ls[k] : null,
    setItem:    (k,v) => { _ls[k] = String(v); },
    removeItem: k     => { delete _ls[k]; },
    clear:      ()    => { for (const k in _ls) delete _ls[k]; },
};
vi.mock('./auth.js', () => ({ solidSession: { info: { isLoggedIn: false } }, podStorageRoot: () => null }));
vi.mock('./pod.js', () => ({ podPublishIdentityAcl: async () => true }));

import {
    E2EDecryptError, initE2E, myX25519PubB64u, cachePeerPub, ratchetEncrypt, ratchetDecrypt,
    loadRatchetState, b64uEnc, MAX_SKIP, _resetForTesting,
} from './e2e.js';
import * as E2E from './e2e.js';

async function identity() {
    localStorage.clear();
    _resetForTesting();
    await initE2E();
    return {
        pub: myX25519PubB64u(),
        jwk: localStorage.getItem('proxion_e2e_x25519_priv_jwk'),
    };
}
// Switch the module to one side of the conversation. Each side keeps its own
// localStorage snapshot so states never mix.
const sides = {};
async function become(name) {
    if (become.current) sides[become.current].ls = { ..._ls };
    _resetForTesting();
    localStorage.clear();
    Object.assign(_ls, sides[name].ls);
    await initE2E();
    become.current = name;
}
async function setup() {
    become.current = null;
    const a = await identity(); sides.alice = { ls: { ..._ls } };
    const b = await identity(); sides.bob = { ls: { ..._ls } };
    await become('alice');
    cachePeerPub('bob', b.pub);
    await become('bob');
    cachePeerPub('alice', a.pub);
    await become('alice');
}
const dec = (m) => ratchetDecrypt('alice', m.ciphertext, m.nonce, m.msgNum, m.ratchetPub, m.pn);
const junk = () => b64uEnc(crypto.getRandomValues(new Uint8Array(48)));
const nonce = () => b64uEnc(crypto.getRandomValues(new Uint8Array(12)));

beforeEach(setup);

describe('skipped message keys', () => {
    it('keeps decrypting out-of-order messages after more than MAX_SKIP are lost for good', async () => {
        const sent = [];
        for (let i = 0; i <= 2 * MAX_SKIP + 1; i++) sent.push(await ratchetEncrypt('bob', 'm' + i));
        await become('bob');
        expect(await dec(sent[0])).toBe('m0');
        // Messages 1..MAX_SKIP-1 never arrive; MAX_SKIP arrives.
        expect(await dec(sent[MAX_SKIP])).toBe('m' + MAX_SKIP);
        // Another run is lost; the buffer now holds more than MAX_SKIP keys in total.
        expect(await dec(sent[2 * MAX_SKIP])).toBe('m' + 2 * MAX_SKIP);
        // A late message from the second gap still decrypts.
        expect(await dec(sent[MAX_SKIP + 1])).toBe('m' + (MAX_SKIP + 1));
        expect(await dec(sent[2 * MAX_SKIP + 1])).toBe('m' + (2 * MAX_SKIP + 1));
    });

    it('a new chain decrypts even when more than MAX_SKIP messages of the old chain were lost', async () => {
        const sent = [];
        for (let i = 0; i < MAX_SKIP + 10; i++) sent.push(await ratchetEncrypt('bob', 'a' + i));
        await become('bob');
        expect(await dec(sent[0])).toBe('a0');
        const reply = await ratchetEncrypt('alice', 'bob reply');
        await become('alice');
        expect(await ratchetDecrypt('bob', reply.ciphertext, reply.nonce, reply.msgNum, reply.ratchetPub, reply.pn))
            .toBe('bob reply');
        const next = await ratchetEncrypt('bob', 'new chain');
        expect(next.pn).toBe(MAX_SKIP + 10);
        await become('bob');
        expect(await dec(next)).toBe('new chain');
    });

    it('caps the stored skipped keys, evicting the oldest first', async () => {
        const cap = E2E.MAX_SKIPPED_KEYS;
        expect(Number.isInteger(cap)).toBe(true);
        const sent = [];
        const n = cap + 3 * MAX_SKIP;
        for (let i = 0; i <= n; i++) sent.push(await ratchetEncrypt('bob', 'm' + i));
        await become('bob');
        for (let i = 0; i <= n; i += MAX_SKIP) expect(await dec(sent[i])).toBe('m' + i);
        const st = await loadRatchetState('alice');
        expect(Object.keys(st.skippedKeys).length).toBeLessThanOrEqual(cap);
        // The oldest gap was evicted; a recent one is still there.
        await expect(dec(sent[1])).rejects.toThrow(E2EDecryptError);
        const recent = sent[Math.floor(n / MAX_SKIP) * MAX_SKIP - 1];
        expect(await dec(recent)).toBe('m' + recent.msgNum);
    });

    it('drops keys of chains older than the previous ratchet step', async () => {
        // Chain 1 of Alice: Bob loses message 1.
        const c1 = [await ratchetEncrypt('bob', 'c1-0'), await ratchetEncrypt('bob', 'c1-1'), await ratchetEncrypt('bob', 'c1-2')];
        await become('bob');
        await dec(c1[0]); await dec(c1[2]);
        for (let round = 0; round < 2; round++) {
            const r = await ratchetEncrypt('alice', 'r' + round);
            await become('alice');
            await ratchetDecrypt('bob', r.ciphertext, r.nonce, r.msgNum, r.ratchetPub, r.pn);
            const m = await ratchetEncrypt('bob', 'n' + round);
            await become('bob');
            expect(await dec(m)).toBe('n' + round);
        }
        // Two ratchet steps later, the chain-1 key is gone.
        const st = await loadRatchetState('alice');
        expect(Object.keys(st.skippedKeys).some((k) => k.startsWith(c1[0].ratchetPub + ':'))).toBe(false);
    });
});

describe('late messages from an older chain', () => {
    it('decrypt from the buffer without ratcheting the session back', async () => {
        const old = [await ratchetEncrypt('bob', 'o0'), await ratchetEncrypt('bob', 'o1')];
        await become('bob');
        expect(await dec(old[0])).toBe('o0');
        const r = await ratchetEncrypt('alice', 'reply');
        await become('alice');
        await ratchetDecrypt('bob', r.ciphertext, r.nonce, r.msgNum, r.ratchetPub, r.pn);
        const n0 = await ratchetEncrypt('bob', 'n0');
        const n1 = await ratchetEncrypt('bob', 'n1');
        await become('bob');
        expect(await dec(n0)).toBe('n0');
        expect(await dec(old[1])).toBe('o1');   // straggler from the previous chain
        expect(await dec(n1)).toBe('n1');       // the current chain still works
    });
});

describe('skip work bound (anti-DoS)', () => {
    it('rejects a huge forged msg_num without deriving keys', async () => {
        const m0 = await ratchetEncrypt('bob', 'seed');
        await become('bob');
        await dec(m0);
        const t = performance.now();
        await expect(ratchetDecrypt('alice', junk(), nonce(), 1e12, m0.ratchetPub, 0))
            .rejects.toThrow(E2EDecryptError);
        expect(performance.now() - t).toBeLessThan(1000);
        expect(await dec(await (async () => { await become('alice'); const m = await ratchetEncrypt('bob', 'after'); await become('bob'); return m; })()))
            .toBe('after');
    });

    it('a huge forged pn on a new ratchet key is bounded and leaves the session intact', async () => {
        const m0 = await ratchetEncrypt('bob', 'seed');
        const m1 = await ratchetEncrypt('bob', 'second');
        await become('bob');
        await dec(m0);
        const forged = (await crypto.subtle.exportKey('jwk',
            (await crypto.subtle.generateKey({ name: 'X25519' }, true, ['deriveBits'])).publicKey)).x;
        const t = performance.now();
        await expect(ratchetDecrypt('alice', junk(), nonce(), 0, forged, Number.MAX_SAFE_INTEGER))
            .rejects.toThrow(E2EDecryptError);
        expect(performance.now() - t).toBeLessThan(1000);
        expect(await dec(m1)).toBe('second');
    });

    it('rejects a msg_num gap larger than MAX_SKIP in one chain', async () => {
        const sent = [];
        for (let i = 0; i <= MAX_SKIP + 2; i++) sent.push(await ratchetEncrypt('bob', 'm' + i));
        await become('bob');
        await dec(sent[0]);
        await expect(dec(sent[MAX_SKIP + 2])).rejects.toThrow(E2EDecryptError);
        expect(await dec(sent[MAX_SKIP + 1])).toBe('m' + (MAX_SKIP + 1));
    });

    it('rejects non-integer or negative header numbers', async () => {
        const m0 = await ratchetEncrypt('bob', 'seed');
        await become('bob');
        await dec(m0);
        for (const bad of [-1, 1.5, NaN, Infinity]) {
            await expect(ratchetDecrypt('alice', junk(), nonce(), bad, m0.ratchetPub, 0))
                .rejects.toThrow(E2EDecryptError);
        }
    });
});
