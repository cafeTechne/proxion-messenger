import * as E2E from './e2e.js';
import { createAuthContextTracker } from './auth-context.js';
// e2e-account-namespace.test.js (R134) — the E2E identity key, cached peer keys
// and verification badges are namespaced per signed-in Solid account, so two
// accounts on one browser never share an identity. The migration is
// non-destructive: the FIRST account claims the pre-existing legacy identity and
// the legacy record is never deleted (a regenerated identity key is irreversible).
import 'fake-indexeddb/auto';
import { describe, it, expect, beforeEach } from 'vitest';
import { vi } from 'vitest';

// ── localStorage mock (supports length/key, needed by the cache migration) ─────
function makeLS() {
    const m = new Map();
    return {
        getItem: (k) => (m.has(k) ? m.get(k) : null),
        setItem: (k, v) => { m.set(k, String(v)); },
        removeItem: (k) => { m.delete(k); },
        clear: () => m.clear(),
        key: (i) => (Array.from(m.keys())[i] ?? null),
        get length() { return m.size; },
    };
}
globalThis.localStorage = makeLS();

// ── Mutable auth session so a test can switch accounts ─────────────────────────
let _sess = { info: { isLoggedIn: false } };
let _root = null;
let _authTracker = createAuthContextTracker(() => _sess.info);
vi.mock('./auth.js', () => ({
    authContext: () => _authTracker.current(),
    get solidSession() { return _sess; },
    podStorageRoot: () => _root,
}));
vi.mock('./pod.js', () => ({ podPublishIdentityAcl: async () => true }));

import {
    initE2E, _resetForTesting, myX25519PubB64u, cachePeerPub, ratchetEncrypt, fetchAndCachePeerPub, saveRatchetState, pendingDmReceives, saveDmSendJob, listDmSendJobs,
} from './e2e.js';

const WEBID_A = 'https://alice.example/profile/card#me';
const WEBID_B = 'https://bob.example/profile/card#me';
const KEY_DB = 'proxion-e2e-keys';
const KEY_STORE = 'keys';

function login(webId) { _authTracker.note('login'); _sess = { info: { isLoggedIn: true, webId } }; }
function logout() { _authTracker.note('logout'); _sess = { info: { isLoggedIn: false } }; }

// Read a record straight from IndexedDB (to assert the legacy 'self' survives).
function idbGet(recKey) {
    return new Promise((res) => {
        const req = indexedDB.open(KEY_DB, 1);
        req.onupgradeneeded = (e) => { const db = e.target.result; if (!db.objectStoreNames.contains(KEY_STORE)) db.createObjectStore(KEY_STORE); };
        req.onsuccess = (e) => {
            const db = e.target.result;
            try {
                const r = db.transaction(KEY_STORE, 'readonly').objectStore(KEY_STORE).get(recKey);
                r.onsuccess = () => { db.close(); res(r.result || null); };
                r.onerror = () => { db.close(); res(null); };
            } catch { db.close(); res(null); }
        };
        req.onerror = () => res(null);
    });
}
function clearKeyStore() {
    return new Promise((res) => {
        const req = indexedDB.open(KEY_DB, 1);
        req.onupgradeneeded = (e) => { const db = e.target.result; if (!db.objectStoreNames.contains(KEY_STORE)) db.createObjectStore(KEY_STORE); };
        req.onsuccess = (e) => {
            const db = e.target.result;
            try {
                const tx = db.transaction(KEY_STORE, 'readwrite');
                tx.objectStore(KEY_STORE).clear();
                tx.oncomplete = () => { db.close(); res(); };
                tx.onerror = () => { db.close(); res(); };
            } catch { db.close(); res(); }
        };
        req.onerror = () => res();
    });
}

// Boot the module afresh for a given session (mimics a page reload).
async function boot(webId) {
    _resetForTesting(); _authTracker = createAuthContextTracker(() => _sess.info);
    if (webId) login(webId); else logout();
    await initE2E();
    return myX25519PubB64u();
}

describe('E2E per-account identity namespacing', () => {
    beforeEach(async () => { _resetForTesting(); _authTracker = createAuthContextTracker(() => _sess.info); logout(); localStorage.clear(); await clearKeyStore(); });

    it('logged out uses the legacy self record and sets no account marker', async () => {
        const pub = await boot(null);
        expect(pub).toBeTruthy();
        expect(await idbGet('self')).toBeTruthy();
        expect(await idbGet('self::' + WEBID_A)).toBe(null);
        expect(localStorage.getItem('proxion_e2e_legacy_claimed')).toBe(null);
    });

    it('first account claims the pre-existing legacy identity (same pub); legacy is never deleted', async () => {
        // A user who has been running the app has a legacy 'self' identity.
        const legacyPub = await boot(null);
        const legacyRec = await idbGet('self');

        // First sign-in adopts that exact identity.
        const aPub = await boot(WEBID_A);
        expect(aPub).toBe(legacyPub);
        expect(localStorage.getItem('proxion_e2e_legacy_claimed')).toBe(WEBID_A);
        expect(await idbGet('self::' + WEBID_A)).toBeTruthy();

        // Non-destructive: the legacy record still exists, unchanged.
        const legacyAfter = await idbGet('self');
        expect(legacyAfter).toBeTruthy();
        expect(legacyAfter.x25519Pub).toBe(legacyRec.x25519Pub);
    });

    it('a second account gets a DISTINCT identity, never adopting the first', async () => {
        const legacyPub = await boot(null);
        const aPub = await boot(WEBID_A);
        const bPub = await boot(WEBID_B);
        expect(aPub).toBe(legacyPub);
        expect(bPub).not.toBe(aPub);
        expect(bPub).toBeTruthy();
        // The claim marker still points at the first account, not B.
        expect(localStorage.getItem('proxion_e2e_legacy_claimed')).toBe(WEBID_A);
        // Both accounts have their own stored record.
        expect(await idbGet('self::' + WEBID_A)).toBeTruthy();
        expect(await idbGet('self::' + WEBID_B)).toBeTruthy();
    });

    it('an account identity persists across re-login (loaded, not regenerated)', async () => {
        await boot(null);
        const aPub = await boot(WEBID_A);
        const bPub = await boot(WEBID_B);
        expect(await boot(WEBID_A)).toBe(aPub);   // A again
        expect(await boot(WEBID_B)).toBe(bPub);   // B again
        expect(await boot(WEBID_A)).toBe(aPub);   // and still A
    });

    it('with no legacy identity, each account generates its own distinct key', async () => {
        // Fresh browser: no legacy 'self' at all.
        const aPub = await boot(WEBID_A);
        const bPub = await boot(WEBID_B);
        expect(aPub).toBeTruthy();
        expect(bPub).toBeTruthy();
        expect(aPub).not.toBe(bPub);
        // The first account to sign in with no legacy still records the claim, so a
        // later legacy import can't be adopted by a different account.
        expect(localStorage.getItem('proxion_e2e_legacy_claimed')).toBe(null);
        expect(await idbGet('self')).toBe(null);   // nothing was created at legacy
    });

    it('ratchet state is written under the account namespace', async () => {
        await boot(WEBID_A);
        // A minimal 32-byte X25519 pub for the peer (value only needs to be valid b64u).
        const kp = await crypto.subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
        const peerPub = (await crypto.subtle.exportKey('jwk', kp.publicKey)).x;
        cachePeerPub('bob', peerPub);
        await ratchetEncrypt('bob', 'hi');
        expect(localStorage.getItem('proxion_e2e_state_bob::' + WEBID_A)).toBeTruthy();
        expect(localStorage.getItem('proxion_e2e_state_bob')).toBe(null);   // not the bare key
        expect(localStorage.getItem('proxion_e2e_peer_pub_bob::' + WEBID_A)).toBe(peerPub);
    });

    it('first account migrates legacy peer caches into its namespace; a second account cannot see them', async () => {
        // Legacy (un-namespaced) trust marks from before the upgrade.
        localStorage.setItem('proxion_e2e_verified_bob', '1');
        localStorage.setItem('proxion_e2e_peer_pub_bob', 'LEGACYPEERPUB');
        localStorage.setItem('proxion_verified_did:key:zCarol', '55555 66666');
        await boot(null);   // establish a legacy identity to claim

        await boot(WEBID_A);
        // Migrated into A's namespace and removed from the bare names.
        expect(localStorage.getItem('proxion_e2e_verified_bob::' + WEBID_A)).toBe('1');
        expect(localStorage.getItem('proxion_e2e_peer_pub_bob::' + WEBID_A)).toBe('LEGACYPEERPUB');
        expect(localStorage.getItem('proxion_verified_did:key:zCarol::' + WEBID_A)).toBe('55555 66666');
        expect(localStorage.getItem('proxion_e2e_verified_bob')).toBe(null);
        expect(localStorage.getItem('proxion_e2e_peer_pub_bob')).toBe(null);
        expect(localStorage.getItem('proxion_verified_did:key:zCarol')).toBe(null);

        await boot(WEBID_B);
        // B sees none of A's marks.
        expect(localStorage.getItem('proxion_e2e_verified_bob::' + WEBID_B)).toBe(null);
        expect(localStorage.getItem('proxion_e2e_peer_pub_bob::' + WEBID_B)).toBe(null);
        expect(localStorage.getItem('proxion_verified_did:key:zCarol::' + WEBID_B)).toBe(null);
    });
});


describe('peer-key discovery keeps its initiating account', () => {
    beforeEach(() => { _resetForTesting(); _authTracker = createAuthContextTracker(() => _sess.info); logout(); _root = null; localStorage.clear(); });
    const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
    it.each(['response', 'json'])('ignores an account switch while waiting for %s', async stage => {
        login(WEBID_A); _root = 'https://alice.example/';
        const waiting = deferred();
        const response = { ok: true, json: vi.fn(() => stage === 'json' ? waiting.promise : Promise.resolve({ version: 1, pub: 'late-key' })) };
        _sess.fetch = vi.fn(() => stage === 'response' ? waiting.promise : Promise.resolve(response));
        const read = fetchAndCachePeerPub('peer', 'https://peer.example/');
        if (stage === 'json') await vi.waitFor(() => expect(response.json).toHaveBeenCalled());
        login(WEBID_B); _root = 'https://bob.example/';
        localStorage.setItem('proxion_e2e_peer_pub_peer::' + WEBID_B, 'trusted-bob-key');
        localStorage.setItem('proxion_e2e_verified_peer::' + WEBID_B, 'trusted-bob-key');
        waiting.resolve(stage === 'response' ? response : { version: 1, pub: 'late-key' });
        expect(await read).toBe(false);
        expect(localStorage.getItem('proxion_e2e_peer_pub_peer::' + WEBID_B)).toBe('trusted-bob-key');
        expect(localStorage.getItem('proxion_e2e_verified_peer::' + WEBID_B)).toBe('trusted-bob-key');
        expect(localStorage.getItem('proxion_e2e_peer_pub_peer::' + WEBID_A)).toBeNull();
    });
    it('does not populate the unscoped cache after the session expires', async () => {
        login(WEBID_A); _root = 'https://alice.example/';
        const waiting = deferred(); _sess.fetch = () => waiting.promise;
        const read = fetchAndCachePeerPub('peer', 'https://peer.example/');
        _sess.info.isLoggedIn = false;
        waiting.resolve({ ok: true, json: async () => ({ version: 1, pub: 'late-key' }) });
        expect(await read).toBe(false);
        expect(localStorage.getItem('proxion_e2e_peer_pub_peer')).toBeNull();
    });
    it('ignores a lookup from an expired bridge generation', async () => {
        const waiting = deferred();
        let current = true;
        const read = fetchAndCachePeerPub('peer', 'https://peer.example/', {
            fetch: () => waiting.promise, ownRoot: 'https://alice.example/',
            current: () => current,
        });
        current = false;
        waiting.resolve({ ok: true, json: async () => ({ version: 1, pub: 'late-key' }) });
        expect(await read).toBe(false);
        expect(localStorage.getItem('proxion_e2e_peer_pub_peer')).toBeNull();
    });
    it('ignores a lookup after the same account changes pod root', async () => {
        login(WEBID_A); _root = 'https://alice.example/';
        const waiting = deferred();
        _sess.fetch = () => waiting.promise;
        const read = fetchAndCachePeerPub('peer', 'https://peer.example/');
        _root = 'https://other.example/';
        waiting.resolve({ ok: true, json: async () => ({ version: 1, pub: 'late-key' }) });
        expect(await read).toBe(false);
        expect(localStorage.getItem('proxion_e2e_peer_pub_peer::' + WEBID_A)).toBeNull();
    });
    it('accepts a lookup that finishes in the original account', async () => {
        login(WEBID_A); _root = 'https://alice.example/';
        _sess.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ version: 1, pub: 'current-key' }) }));
        expect(await fetchAndCachePeerPub('peer', 'https://peer.example/')).toBe(true);
        expect(localStorage.getItem('proxion_e2e_peer_pub_peer::' + WEBID_A)).toBe('current-key');
    });
});


describe('gateway recovery account namespace', () => {
    beforeEach(async () => {
        _resetForTesting(); _authTracker = createAuthContextTracker(() => _sess.info); logout(); _root = null; localStorage.clear(); await clearKeyStore();
    });
    it('keeps encrypted outgoing drafts scoped to their initiating account', async () => {
        await boot(WEBID_A);
        await saveDmSendJob({ id: 'alice-send', scope: 'alice/device', record: { content: 'alice-private-draft' } });
        expect((await listDmSendJobs()).map(job => job.id)).toEqual(['alice-send']);
        await boot(WEBID_B); expect(await listDmSendJobs()).toEqual([]);
        await boot(null); expect(await listDmSendJobs()).toEqual([]);
        await boot(WEBID_A); expect((await listDmSendJobs())[0].record.content).toBe('alice-private-draft');
    });

    it('scans only the active account recovery records, including after logout', async () => {
        await boot(WEBID_A);
        const id = 'a'.repeat(64);
        await saveRatchetState('alice-peer', { rootKey: [1], pendingReceives: { [id]: 'alice-private' },
            pendingReceiveData: { [id]: { transport: 'gateway', scope: 'alice/device', message: { message_id: 'a' } } } });
        expect((await pendingDmReceives()).map(row => row.content)).toEqual(['alice-private']);
        await boot(WEBID_B);
        expect(await pendingDmReceives()).toEqual([]);
        await boot(null);
        expect(await pendingDmReceives()).toEqual([]);
        await boot(WEBID_A);
        expect((await pendingDmReceives()).map(row => row.content)).toEqual(['alice-private']);
    });
});

describe('live E2E lifecycle isolation without module resets', () => {
    beforeEach(async () => {
        _resetForTesting(); _authTracker = createAuthContextTracker(() => _sess.info);
        logout(); _root = null; localStorage.clear(); await clearKeyStore();
    });
    async function peerKey() {
        const pair = await crypto.subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
        return (await crypto.subtle.exportKey('jwk', pair.publicKey)).x;
    }
    it('reopens each account identity, ratchet and pending job on a live switch back', async () => {
        login(WEBID_A); await initE2E(); const a = myX25519PubB64u();
        const peerPub = await peerKey();
        await ratchetEncrypt('shared-peer', 'from A', { peerPub });
        await saveDmSendJob({ id: 'a-job', content: 'A only' });
        login(WEBID_B);
        expect(myX25519PubB64u()).toBeNull();
        await initE2E(); expect(myX25519PubB64u()).not.toBe(a);
        expect(await listDmSendJobs()).toEqual([]);
        expect((await ratchetEncrypt('shared-peer', 'from B', { peerPub })).msgNum).toBe(0);
        login(WEBID_A); await initE2E(); expect(myX25519PubB64u()).toBe(a);
        expect((await listDmSendJobs()).map(job => job.id)).toEqual(['a-job']);
        expect((await ratchetEncrypt('shared-peer', 'A again', { peerPub })).msgNum).toBe(1);
    });
    it('keeps the same identity and send counter through expiry, pausing encrypted work', async () => {
        login(WEBID_A); await initE2E(); const a = myX25519PubB64u(), peerPub = await peerKey();
        await ratchetEncrypt('peer', 'before expiry', { peerPub });
        await saveDmSendJob({ id: 'pending' });
        _sess.info.isLoggedIn = false;
        expect(E2E.e2eScopedKey('key')).toBe('key::' + WEBID_A);
        await expect(listDmSendJobs()).rejects.toMatchObject({ name: 'E2EContextError' });
        await expect(ratchetEncrypt('peer', 'expired', { peerPub })).rejects.toMatchObject({ name: 'E2EContextError' });
        login(WEBID_A); await initE2E(); expect(myX25519PubB64u()).toBe(a);
        expect((await listDmSendJobs())[0].id).toBe('pending');
        expect((await ratchetEncrypt('peer', 'after expiry', { peerPub })).msgNum).toBe(1);
        expect(localStorage.getItem('proxion_e2e_state_peer')).toBeNull();
    });
    it('does not let a delayed initializer replace another account identity', async () => {
        const original = crypto.subtle.generateKey.bind(crypto.subtle);
        let release, started;
        const waiting = new Promise(resolve => { release = resolve; });
        const entered = new Promise(resolve => { started = resolve; });
        const spy = vi.spyOn(crypto.subtle, 'generateKey').mockImplementationOnce(async (...args) => {
            started(); await waiting; return original(...args);
        });
        try {
            login(WEBID_A); const pending = initE2E().catch(err => err); await entered;
            login(WEBID_B); await initE2E(); const b = myX25519PubB64u();
            release(); expect(await pending).toMatchObject({ name: 'E2EContextError' });
            expect(myX25519PubB64u()).toBe(b);
            expect(await idbGet('self::' + WEBID_A)).toBeNull();
        } finally { release(); spy.mockRestore(); }
    });
    it.each(['switch', 'replacement', 'return'])('cancels delayed encryption after %s and leaves counters retryable', async kind => {
        login(WEBID_A); await initE2E(); const peerPub = await peerKey();
        const original = crypto.subtle.encrypt.bind(crypto.subtle);
        let release, started;
        const waiting = new Promise(resolve => { release = resolve; });
        const entered = new Promise(resolve => { started = resolve; });
        const spy = vi.spyOn(crypto.subtle, 'encrypt').mockImplementationOnce(async (...args) => {
            started(); await waiting; return original(...args);
        });
        try {
            const pending = ratchetEncrypt('peer', 'stale message', { peerPub }).catch(err => err); await entered;
            if (kind !== 'replacement') { login(WEBID_B); await initE2E(); }
            if (kind !== 'switch') { login(WEBID_A); await initE2E(); }
            release(); expect(await pending).toBeInstanceOf(Error);
            expect(localStorage.getItem(E2E.e2eScopedKey('proxion_e2e_state_peer'))).toBeNull();
            expect((await ratchetEncrypt('peer', 'valid message', { peerPub })).msgNum).toBe(0);
        } finally { release(); spy.mockRestore(); }
    });
    it('isolates and preserves scoped fallback identities while IndexedDB is unavailable', async () => {
        const database = globalThis.indexedDB;
        globalThis.indexedDB = undefined;
        try {
            login(WEBID_A); await initE2E(); const a = myX25519PubB64u();
            login(WEBID_B); await initE2E(); expect(myX25519PubB64u()).not.toBe(a);
            login(WEBID_A); await initE2E(); expect(myX25519PubB64u()).toBe(a);
            globalThis.indexedDB = database;
            _resetForTesting(); await initE2E(); expect(myX25519PubB64u()).toBe(a);
        } finally { globalThis.indexedDB = database; }
    });
});

it('concurrent initializers in two tabs retain the one committed account identity', async () => {
    _resetForTesting(); _authTracker = createAuthContextTracker(() => _sess.info);
    localStorage.clear(); await clearKeyStore(); login(WEBID_A);
    const otherTab = await import('./e2e.js?concurrent-tab');
    try {
        await Promise.all([initE2E(), otherTab.initE2E()]);
        expect(myX25519PubB64u()).toBe(otherTab.myX25519PubB64u());
        expect((await idbGet('self::' + WEBID_A)).x25519Pub).toBe(myX25519PubB64u());
    } finally { otherTab._resetForTesting(); }
});

it('cancels a delayed first decrypt without using the replacement account private key', async () => {
    _resetForTesting(); _authTracker = createAuthContextTracker(() => _sess.info);
    localStorage.clear(); await clearKeyStore(); login(WEBID_A); await initE2E();
    const a = myX25519PubB64u();
    login(WEBID_B); await initE2E();
    const wire = await ratchetEncrypt('alice', 'private for Alice', { peerPub: a });
    login(WEBID_A); await initE2E();
    const original = crypto.subtle.importKey.bind(crypto.subtle);
    let release, started;
    const waiting = new Promise(resolve => { release = resolve; });
    const entered = new Promise(resolve => { started = resolve; });
    const spy = vi.spyOn(crypto.subtle, 'importKey').mockImplementationOnce(async (...args) => {
        started(); await waiting; return original(...args);
    });
    try {
        const pending = E2E.ratchetDecrypt('bob', wire.ciphertext, wire.nonce, wire.msgNum, wire.ratchetPub, wire.pn).catch(err => err);
        await entered; login(WEBID_B); await initE2E(); release();
        expect(await pending).toBeInstanceOf(Error);
        expect(localStorage.getItem(E2E.e2eScopedKey('proxion_e2e_state_bob'))).toBeNull();
        login(WEBID_A); await initE2E();
        expect(await E2E.ratchetDecrypt('bob', wire.ciphertext, wire.nonce, wire.msgNum, wire.ratchetPub, wire.pn)).toBe('private for Alice');
    } finally { release(); spy.mockRestore(); }
});

it('lets a new account send while old same-peer work is blocked, cancelling old queued work', async () => {
    _resetForTesting(); _authTracker = createAuthContextTracker(() => _sess.info);
    localStorage.clear(); await clearKeyStore(); login(WEBID_A); await initE2E();
    const pair = await crypto.subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
    const peerPub = (await crypto.subtle.exportKey('jwk', pair.publicKey)).x;
    const original = crypto.subtle.encrypt.bind(crypto.subtle);
    let release, started;
    const waiting = new Promise(resolve => { release = resolve; });
    const entered = new Promise(resolve => { started = resolve; });
    const spy = vi.spyOn(crypto.subtle, 'encrypt').mockImplementationOnce(async (...args) => {
        started(); await waiting; return original(...args);
    });
    try {
        const pending = ratchetEncrypt('same-peer', 'A old', { peerPub }).catch(err => err);
        await entered;
        const queued = ratchetEncrypt('same-peer', 'A queued', { peerPub }).catch(err => err);
        login(WEBID_B); await initE2E();
        expect((await ratchetEncrypt('same-peer', 'B now', { peerPub })).msgNum).toBe(0);
        release(); expect(await pending).toBeInstanceOf(Error); expect(await queued).toBeInstanceOf(Error);
        login(WEBID_A); await initE2E();
        expect((await ratchetEncrypt('same-peer', 'A retry', { peerPub })).msgNum).toBe(0);
    } finally { release(); spy.mockRestore(); }
});

it('rejects crypto work while the tab no longer owns the ratchet', async () => {
    _resetForTesting(); _authTracker = createAuthContextTracker(() => _sess.info);
    localStorage.clear(); await clearKeyStore(); login(WEBID_A); await initE2E();
    let active = true;
    E2E.setE2EActivityCheck(() => active);
    await saveDmSendJob({ id: 'still-pending' });
    active = false;
    await expect(listDmSendJobs()).rejects.toMatchObject({ name: 'E2EContextError' });
    active = true;
    expect((await listDmSendJobs())[0].id).toBe('still-pending');
});
