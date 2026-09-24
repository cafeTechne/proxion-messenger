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
vi.mock('./auth.js', () => ({
    get solidSession() { return _sess; },
    podStorageRoot: () => null,
}));
vi.mock('./pod.js', () => ({ podPublishIdentityAcl: async () => true }));

import {
    initE2E, _resetForTesting, myX25519PubB64u, cachePeerPub, ratchetEncrypt,
} from './e2e.js';

const WEBID_A = 'https://alice.example/profile/card#me';
const WEBID_B = 'https://bob.example/profile/card#me';
const KEY_DB = 'proxion-e2e-keys';
const KEY_STORE = 'keys';

function login(webId) { _sess = { info: { isLoggedIn: true, webId } }; }
function logout() { _sess = { info: { isLoggedIn: false } }; }

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
    _resetForTesting();
    if (webId) login(webId); else logout();
    await initE2E();
    return myX25519PubB64u();
}

describe('E2E per-account identity namespacing', () => {
    beforeEach(async () => { _resetForTesting(); logout(); localStorage.clear(); await clearKeyStore(); });

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
