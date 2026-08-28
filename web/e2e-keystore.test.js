// Verifies the R110 key store: the X25519 identity + state key live as
// non-extractable CryptoKeys in IndexedDB, an existing localStorage keypair is
// migrated once and its scalar removed, and the identity persists across re-init.
import 'fake-indexeddb/auto';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const _ls = {};
globalThis.localStorage = {
    getItem: (k) => Object.prototype.hasOwnProperty.call(_ls, k) ? _ls[k] : null,
    setItem: (k, v) => { _ls[k] = String(v); },
    removeItem: (k) => { delete _ls[k]; },
    clear: () => { for (const k in _ls) delete _ls[k]; },
};

vi.mock('./auth.js', () => ({
    solidSession: { info: { isLoggedIn: false } },
    podStorageRoot: () => null,
}));

import { initE2E, _resetForTesting, ratchetEncrypt, cachePeerPub, myX25519PubB64u, e2eSupported } from './e2e.js';

async function makeX25519Pair() {
    const kp = await crypto.subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
    const pubJwk = await crypto.subtle.exportKey('jwk', kp.publicKey);
    const privJwk = await crypto.subtle.exportKey('jwk', kp.privateKey);
    return { privJwk, pubB64u: pubJwk.x };
}
// Clear the stored record via a short-lived connection (deleteDatabase would block
// on the module's own open connection and hang).
function clearKeyDb() {
    return new Promise((res) => {
        const req = indexedDB.open('proxion-e2e-keys', 1);
        req.onupgradeneeded = (e) => { const db = e.target.result; if (!db.objectStoreNames.contains('keys')) db.createObjectStore('keys'); };
        req.onsuccess = (e) => {
            const db = e.target.result;
            try {
                const tx = db.transaction('keys', 'readwrite');
                tx.objectStore('keys').delete('self');
                tx.oncomplete = () => { db.close(); res(); };
                tx.onerror = () => { db.close(); res(); };
            } catch { db.close(); res(); }
        };
        req.onerror = () => res();
    });
}

describe('e2e key store (IndexedDB, non-extractable)', () => {
    beforeEach(async () => { _resetForTesting(); localStorage.clear(); await clearKeyDb(); });

    it('fresh init: keys go to IndexedDB, no private scalar in localStorage, key usable', async () => {
        await initE2E();
        expect(e2eSupported).toBe(true);
        expect(localStorage.getItem('proxion_e2e_x25519_priv_jwk')).toBe(null);
        expect(myX25519PubB64u()).toBeTruthy();
        // The key works for ECDH (encrypt to a peer succeeds).
        const bob = await makeX25519Pair();
        cachePeerPub('bob', bob.pubB64u);
        const enc = await ratchetEncrypt('bob', 'hello');
        expect(enc.ciphertext).toBeTruthy();
    });

    it('migrates a legacy localStorage keypair and removes the scalar, same identity', async () => {
        const id = await makeX25519Pair();
        localStorage.setItem('proxion_e2e_x25519_priv_jwk', JSON.stringify(id.privJwk));
        localStorage.setItem('proxion_e2e_x25519_pub_b64u', id.pubB64u);
        await initE2E();
        expect(localStorage.getItem('proxion_e2e_x25519_priv_jwk')).toBe(null);   // scalar gone
        expect(myX25519PubB64u()).toBe(id.pubB64u);                                // identity preserved
    });

    it('persists the identity across re-init (loads from IndexedDB, not regenerated)', async () => {
        await initE2E();
        const pub = myX25519PubB64u();
        _resetForTesting();   // drops in-memory state + the db handle, keeps stored data
        await initE2E();
        expect(myX25519PubB64u()).toBe(pub);
        expect(localStorage.getItem('proxion_e2e_x25519_priv_jwk')).toBe(null);
    });
});
