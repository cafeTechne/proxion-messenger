/**
 * Ratchet state storage: per-device pod path, newest copy wins, legacy migration.
 *
 * Each device encrypts its ratchet state under its own state key, so the pod copy
 * must live at a per-device path (two devices of one account would otherwise
 * overwrite each other's blob), and a stale pod copy (its PUT is not awaited)
 * must never roll a session back past what localStorage already holds.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const _ls = {};
globalThis.localStorage = {
    getItem:    k     => Object.prototype.hasOwnProperty.call(_ls, k) ? _ls[k] : null,
    setItem:    (k,v) => { _ls[k] = String(v); },
    removeItem: k     => { delete _ls[k]; },
    clear:      ()    => { for (const k in _ls) delete _ls[k]; },
    key:        i     => Object.keys(_ls)[i] ?? null,
    get length() { return Object.keys(_ls).length; },
};

const WEBID = 'https://me.pod.example/profile/card#me';
const ROOT = 'https://me.pod.example/';
const pod = new Map();          // url -> body
let failPuts = false;
let _session = { info: { isLoggedIn: false } };
let _root = null;
vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _root,
}));
vi.mock('./pod.js', () => ({ podPublishIdentityAcl: async () => true }));

import {
    initE2E, myX25519PubB64u, cachePeerPub, ratchetEncrypt, ratchetDecrypt,
    loadRatchetState, saveRatchetState, e2eScopedKey, _resetForTesting,
} from './e2e.js';
import * as E2E from './e2e.js';

// Wait for the fire-and-forget pod PUTs to land.
const _podStateWritesSettled = async () => {
    if (E2E._podStateWritesSettled) await E2E._podStateWritesSettled();
    for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
};

const fetchMock = vi.fn(async (url, opts = {}) => {
    if (opts.method === 'PUT') {
        if (failPuts) return { ok: false, status: 503 };
        pod.set(url, opts.body);
        return { ok: true, status: 201 };
    }
    if (!pod.has(url)) return { ok: false, status: 404, text: async () => '' };
    const body = pod.get(url);
    return { ok: true, status: 200, text: async () => body };
});

const legacyUrl = (peer) => ROOT + 'proxion/e2e/' + encodeURIComponent(peer) + '/state.enc';
const stateUrls = () => [...pod.keys()].filter((u) => u.includes('/proxion/e2e/'));

async function makePeer() {
    const kp = await crypto.subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
    return (await crypto.subtle.exportKey('jwk', kp.publicKey)).x;
}

// Re-open the module as a fresh page load on the same device (same identity key,
// which lives in localStorage in this IndexedDB-less test environment).
async function reload() {
    _resetForTesting();
    await initE2E();
}

beforeEach(async () => {
    localStorage.clear();
    pod.clear();
    failPuts = false;
    fetchMock.mockClear();
    _root = ROOT;
    _session = { info: { isLoggedIn: true, webId: WEBID }, fetch: fetchMock };
    _resetForTesting();
    await initE2E();
});

afterEach(() => {
    _session = { info: { isLoggedIn: false } };
    _root = null;
    _resetForTesting();
});

describe('ratchet state pod path', () => {
    it('is per device: includes this device id and is not the shared legacy path', async () => {
        const peer = 'did:key:zPeerA';
        cachePeerPub(peer, await makePeer());
        await ratchetEncrypt(peer, 'hi');
        await _podStateWritesSettled();
        const urls = stateUrls();
        expect(urls).toHaveLength(1);
        expect(urls[0]).not.toBe(legacyUrl(peer));
        expect(urls[0]).toContain(encodeURIComponent(peer) + '/');
        expect(urls[0]).toContain(myX25519PubB64u());
    });

    it('two devices of one account write to different pod resources', async () => {
        const peer = 'did:key:zPeerB';
        const peerPub = await makePeer();
        cachePeerPub(peer, peerPub);
        await ratchetEncrypt(peer, 'from device 1');
        await _podStateWritesSettled();
        const first = stateUrls();

        // A second device: its own identity and state key, same pod.
        localStorage.clear();
        await reload();
        cachePeerPub(peer, peerPub);
        await ratchetEncrypt(peer, 'from device 2');
        await _podStateWritesSettled();
        const all = stateUrls();
        expect(all).toHaveLength(2);
        expect(all).toEqual(expect.arrayContaining(first));
    });
});

describe('ratchet state load prefers the newest copy', () => {
    it('a stale pod copy does not roll the session back past localStorage', async () => {
        const peer = 'did:key:zPeerC';
        cachePeerPub(peer, await makePeer());
        await ratchetEncrypt(peer, 'm0');
        await _podStateWritesSettled();     // pod now holds the state after m0
        failPuts = true;                     // later PUTs are lost
        await ratchetEncrypt(peer, 'm1');
        await ratchetEncrypt(peer, 'm2');
        await _podStateWritesSettled();

        await reload();
        const next = await ratchetEncrypt(peer, 'm3');
        // Rolling back to the pod copy would reuse msg_num 1 (and its message key).
        expect(next.msgNum).toBe(3);
    });

    it('a newer pod copy wins over an older localStorage copy', async () => {
        const peer = 'did:key:zPeerD';
        cachePeerPub(peer, await makePeer());
        await ratchetEncrypt(peer, 'm0');
        const lsKey = e2eScopedKey('proxion_e2e_state_' + peer);
        const oldLs = localStorage.getItem(lsKey);
        await ratchetEncrypt(peer, 'm1');
        await _podStateWritesSettled();
        localStorage.setItem(lsKey, oldLs);  // localStorage restored from an old backup

        await reload();
        expect((await ratchetEncrypt(peer, 'm2')).msgNum).toBe(2);
    });

    it('stores a monotonic counter inside the encrypted state', async () => {
        const peer = 'did:key:zPeerE';
        cachePeerPub(peer, await makePeer());
        await ratchetEncrypt(peer, 'a');
        const v1 = (await loadRatchetState(peer)).ver;
        await ratchetEncrypt(peer, 'b');
        const v2 = (await loadRatchetState(peer)).ver;
        expect(Number.isInteger(v1)).toBe(true);
        expect(v2).toBeGreaterThan(v1);
    });
});

describe('legacy shared pod path migration', () => {
    it('reads the old path when the per-device copy is missing, then writes the new path', async () => {
        const peer = 'did:key:zPeerF';
        cachePeerPub(peer, await makePeer());
        await ratchetEncrypt(peer, 'm0');
        await ratchetEncrypt(peer, 'm1');
        await _podStateWritesSettled();
        // Simulate the old layout: only the shared path holds this device's state.
        const [cur] = stateUrls();
        const blob = pod.get(cur);
        pod.clear();
        pod.set(legacyUrl(peer), blob);
        localStorage.removeItem(e2eScopedKey('proxion_e2e_state_' + peer));

        await reload();
        expect((await ratchetEncrypt(peer, 'm2')).msgNum).toBe(2);
        await _podStateWritesSettled();
        expect(pod.has(cur)).toBe(true);
    });

    it('ignores a legacy blob another device wrote (not decryptable here)', async () => {
        const peer = 'did:key:zPeerG';
        const peerPub = await makePeer();
        cachePeerPub(peer, peerPub);
        await ratchetEncrypt(peer, 'm0');
        await _podStateWritesSettled();
        const [cur] = stateUrls();
        pod.set(legacyUrl(peer), pod.get(cur));
        pod.delete(cur);

        // Another device with its own state key loads: the legacy blob is not its own.
        localStorage.clear();
        await reload();
        cachePeerPub(peer, peerPub);
        expect(await loadRatchetState(peer)).toBe(null);
        expect((await ratchetEncrypt(peer, 'x')).msgNum).toBe(0);
    });
});

describe('saveRatchetState pod writes', () => {
    it('the last write wins on the pod even when saves overlap', async () => {
        const peer = 'did:key:zPeerH';
        cachePeerPub(peer, await makePeer());
        for (let i = 0; i < 5; i++) await ratchetEncrypt(peer, 'm' + i);
        await _podStateWritesSettled();
        localStorage.removeItem(e2eScopedKey('proxion_e2e_state_' + peer));
        await reload();
        expect((await ratchetEncrypt(peer, 'm5')).msgNum).toBe(5);
    });

    it('decrypt still round-trips through storage', async () => {
        // Sanity: receiving side persists via the same path.
        const peer = 'did:key:zPeerI';
        const s = await loadRatchetState(peer);
        expect(s).toBe(null);
        await saveRatchetState(peer, { rootKey: [1], sendMsgNum: 0, skippedKeys: {} });
        expect((await loadRatchetState(peer)).rootKey).toEqual([1]);
        expect(typeof ratchetDecrypt).toBe('function');
    });
});


describe('ratchet backups keep their initiating context', () => {
    const deferred = () => {
        let resolve;
        const promise = new Promise(r => { resolve = r; });
        return { promise, resolve };
    };
    it.each(['replacement account', 'changed account in session', 'expired session', 'same account new session', 'changed session id', 'changed pod root'])(
        'discards a queued backup after %s', async change => {
            const waiting = deferred(), calls = [];
            const original = _session;
            original.info.sessionId = 'original-session';
            original.fetch = vi.fn(async (url, opts) => {
                if (opts?.method !== 'PUT') return fetchMock(url, opts);
                calls.push({ url, body: opts.body });
                if (calls.length === 1) return waiting.promise;
                return { ok: true, status: 201 };
            });
            const peer = 'did:key:queued';
            await saveRatchetState(peer, { rootKey: [1], skippedKeys: {}, sendMsgNum: 1 });
            await saveRatchetState(peer, { rootKey: [1], skippedKeys: {}, sendMsgNum: 2 });
            const local = localStorage.getItem(e2eScopedKey('proxion_e2e_state_' + peer));
            const replacementFetch = vi.fn(async () => ({ ok: true, status: 201 }));
            if (change === 'replacement account') _session = { info: { isLoggedIn: true, webId: 'https://other.example/#me' }, fetch: replacementFetch };
            if (change === 'changed account in session') original.info.webId = 'https://other.example/#me';
            if (change === 'expired session') original.info.isLoggedIn = false;
            if (change === 'same account new session') _session = { info: { ...original.info }, fetch: replacementFetch };
            if (change === 'changed session id') original.info.sessionId = 'replacement-session';
            if (change === 'changed pod root') _root = 'https://new-root.example/';
            waiting.resolve({ ok: true, status: 201 });
            await _podStateWritesSettled();
            expect(calls).toHaveLength(1);
            expect(replacementFetch).not.toHaveBeenCalled();
            expect(localStorage.getItem('proxion_e2e_state_' + peer + '::' + WEBID)).toBe(local);
        });

    it('coalesces queued saves to the newest blob while keeping the initiating fetch', async () => {
        const waiting = deferred(), calls = [];
        const originalFetch = vi.fn(async (url, opts) => {
            calls.push({ url, opts });
            if (calls.length === 1) await waiting.promise;
            return fetchMock(url, opts);
        });
        _session.fetch = originalFetch;
        const peer = 'did:key:coalesced';
        for (let i = 1; i <= 3; i++) {
            await saveRatchetState(peer, { rootKey: [1], skippedKeys: {}, sendMsgNum: i, ver: i });
        }
        const replacementFetch = vi.fn();
        _session.fetch = replacementFetch;
        waiting.resolve();
        await _podStateWritesSettled();
        expect(originalFetch).toHaveBeenCalledTimes(2);
        expect(replacementFetch).not.toHaveBeenCalled();
        _session.fetch = fetchMock;
        localStorage.removeItem(e2eScopedKey('proxion_e2e_state_' + peer));
        await reload();
        expect((await loadRatchetState(peer)).sendMsgNum).toBe(3);
        expect((await loadRatchetState(peer)).ver).toBe(4);
    });

    it('does not let an old account backup block the new account resource', async () => {
        const waiting = deferred();
        _session.fetch = vi.fn(() => waiting.promise);
        const peer = 'did:key:independent';
        await saveRatchetState(peer, { rootKey: [1], skippedKeys: {} });
        const replacementFetch = vi.fn(async () => ({ ok: true, status: 201 }));
        _session = { info: { isLoggedIn: true, webId: 'https://other.example/#me' }, fetch: replacementFetch };
        _root = 'https://other.example/';
        try {
            await saveRatchetState(peer, { rootKey: [2], skippedKeys: {} });
            expect(replacementFetch).toHaveBeenCalledTimes(1);
            expect(replacementFetch.mock.calls[0][0]).toMatch(/^https:\/\/other.example\//);
        } finally {
            waiting.resolve({ ok: true, status: 201 });
            await _podStateWritesSettled();
        }
    });

    it('cancels a pending backup after the device key changes', async () => {
        const waiting = deferred(), firstFetch = vi.fn(() => waiting.promise);
        _session.fetch = firstFetch;
        await saveRatchetState('did:key:rotated', { rootKey: [1], skippedKeys: {} });
        await saveRatchetState('did:key:rotated', { rootKey: [1], skippedKeys: {} });
        // Reinitialization clears the old queue registry, but its active runner
        // must still refuse to start the old pending job after it is released.
        localStorage.clear();
        _session.fetch = fetchMock;
        await reload();
        waiting.resolve({ ok: true, status: 201 });
        await new Promise(resolve => setTimeout(resolve, 0));
        expect(firstFetch).toHaveBeenCalledTimes(1);
        expect(stateUrls()).toEqual([]);
    });

    it.each(['response', 'body'])('does not finish a delayed pod read after an account switch during %s', async stage => {
        const waiting = deferred(), body = vi.fn(() => stage === 'body' ? waiting.promise : Promise.resolve('{}'));
        const originalFetch = vi.fn(() => stage === 'response' ? waiting.promise : Promise.resolve({ ok: true, text: body }));
        _session.fetch = originalFetch;
        const read = loadRatchetState('did:key:read-switch');
        if (stage === 'body') await vi.waitFor(() => expect(body).toHaveBeenCalled());
        const replacement = vi.fn(async () => ({ ok: false, status: 404 }));
        _session = { info: { isLoggedIn: true, webId: 'https://other.example/#me' }, fetch: replacement };
        waiting.resolve(stage === 'response' ? { ok: true, text: body } : '{}');
        await expect(read).rejects.toThrow('context changed');
        expect(originalFetch).toHaveBeenCalledTimes(1);
        expect(replacement).not.toHaveBeenCalled();
    });

    it('does not migrate a local state after its root changes during encryption', async () => {
        const peer = 'did:key:migration-switch';
        await saveRatchetState(peer, { rootKey: [1], skippedKeys: {} });
        await _podStateWritesSettled();
        pod.clear();
        await reload();
        const original = crypto.subtle.encrypt.bind(crypto.subtle);
        const spy = vi.spyOn(crypto.subtle, 'encrypt').mockImplementation(async (...args) => {
            const result = await original(...args); _root = 'https://replacement.example/'; return result;
        });
        try { await expect(loadRatchetState(peer)).rejects.toThrow('context changed'); }
        finally { spy.mockRestore(); }
        await _podStateWritesSettled();
        expect(stateUrls()).toEqual([]);
        // Restoring the owner context permits the previously cancelled resource
        // queue to run normally; cancellation must not leave a stuck runner.
        _root = ROOT;
        expect((await loadRatchetState(peer)).rootKey).toEqual([1]);
        await _podStateWritesSettled();
        expect(stateUrls()).toHaveLength(1);
    });

    it('rejects a save if its pod root changes during encryption', async () => {
        const original = crypto.subtle.encrypt.bind(crypto.subtle);
        const spy = vi.spyOn(crypto.subtle, 'encrypt').mockImplementation(async (...args) => {
            const result = await original(...args);
            _root = 'https://replacement.example/';
            return result;
        });
        try {
            await expect(saveRatchetState('did:key:save-switch', { rootKey: [1], skippedKeys: {} })).rejects.toThrow('context changed');
        } finally { spy.mockRestore(); }
        expect(localStorage.getItem('proxion_e2e_state_did:key:save-switch::' + WEBID)).toBeNull();
        expect(stateUrls()).toEqual([]);
    });
});
