// logout-purge.test.js — solidLogout must purge pod-derived state so an account
// switch on a shared device leaks nothing: the SW caches (proxion-shell-*), the
// offline send queue, the local plaintext stores (DM history + saved messages),
// and the persisted storage root. Best-effort: a failure in any purge must not
// break the logout.
import 'fake-indexeddb/auto';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Controllable browser auth bundle (see auth-ssrf.test.js).
vi.mock('./solid-authn.bundle.js', () => ({
    default: {
        Session: class {
            constructor() { this.info = { isLoggedIn: true, webId: null }; }
            async handleIncomingRedirect() {}
            async login() {}
            async logout() {}
            async fetch() { return { ok: false, status: 404 }; }
        },
    },
}));

const { podQueueClear } = vi.hoisted(() => ({ podQueueClear: vi.fn(async () => {}) }));
vi.mock('./podqueue.js', () => ({ podQueueClear }));

import { solidLogout } from './auth.js';

function memLocalStorage() {
    const s = {};
    return {
        getItem: (k) => (k in s ? s[k] : null),
        setItem: (k, v) => { s[k] = String(v); },
        removeItem: (k) => { delete s[k]; },
    };
}

describe('solidLogout purges pod-derived state', () => {
    let deleted;
    beforeEach(() => {
        globalThis.localStorage = memLocalStorage();
        deleted = [];
        podQueueClear.mockClear();
        globalThis.caches = {
            keys: async () => ['proxion-shell-v213', 'proxion-shell-v1', 'unrelated-cache'],
            delete: async (k) => { deleted.push(k); return true; },
        };
    });

    it('deletes every proxion-shell cache and clears the offline queue', async () => {
        await solidLogout();
        expect(deleted.sort()).toEqual(['proxion-shell-v1', 'proxion-shell-v213']);
        expect(deleted).not.toContain('unrelated-cache');
        expect(podQueueClear).toHaveBeenCalledTimes(1);
    });

    it('still clears the queue and persisted root when caches is unavailable', async () => {
        delete globalThis.caches;
        localStorage.setItem('proxion_storage_root_v2', 'x');
        await solidLogout();                       // must not throw
        expect(podQueueClear).toHaveBeenCalledTimes(1);
        expect(localStorage.getItem('proxion_storage_root_v2')).toBe(null);
    });

    it('does not let a queue-clear failure break logout', async () => {
        podQueueClear.mockRejectedValueOnce(new Error('idb down'));
        await expect(solidLogout()).resolves.toBeUndefined();
        expect(deleted.sort()).toEqual(['proxion-shell-v1', 'proxion-shell-v213']);
    });

    // The mocked session has no WebID, so accountDbName resolves to the base
    // names here (the per-account isolation is covered in dmhistory/saved tests).
    it('clears the local DM-history and saved-message stores', async () => {
        await idbPut('proxion-dm-history', 'messages', 'message_id', 'thread_id',
            { message_id: 'm1', thread_id: 't', content: 'secret' });
        await idbPut('proxion-saved-messages', 'saved', 'id', null,
            { id: 's1', content: 'bookmark' });
        expect(await idbCount('proxion-dm-history', 'messages')).toBe(1);
        expect(await idbCount('proxion-saved-messages', 'saved')).toBe(1);
        await solidLogout();
        expect(await idbCount('proxion-dm-history', 'messages')).toBe(0);
        expect(await idbCount('proxion-saved-messages', 'saved')).toBe(0);
    });
});

// Open a database, creating the store (and an optional index) if missing, put a
// record, then close so the store-clear under test isn't fighting a live handle.
// A prior solidLogout may have created the database empty (open with no version),
// so bump the version to add the store when it isn't there yet.
function idbPut(dbName, storeName, keyPath, indexName, rec) {
    return new Promise((resolve, reject) => {
        const probe = indexedDB.open(dbName);
        probe.onerror = () => reject(probe.error);
        probe.onsuccess = (e) => {
            const existing = e.target.result;
            const hasStore = existing.objectStoreNames.contains(storeName);
            const nextVer = existing.version + (hasStore ? 0 : 1);
            existing.close();
            const req = indexedDB.open(dbName, nextVer);
            req.onupgradeneeded = (ev) => {
                const db = ev.target.result;
                if (!db.objectStoreNames.contains(storeName)) {
                    const os = db.createObjectStore(storeName, { keyPath });
                    if (indexName) os.createIndex(indexName, indexName, { unique: false });
                }
            };
            req.onsuccess = (ev) => {
                const db = ev.target.result;
                const tx = db.transaction(storeName, 'readwrite');
                tx.objectStore(storeName).put(rec);
                tx.oncomplete = () => { db.close(); resolve(); };
                tx.onerror = () => { db.close(); reject(tx.error); };
            };
            req.onerror = () => reject(req.error);
        };
    });
}

function idbCount(dbName, storeName) {
    return new Promise((resolve) => {
        const req = indexedDB.open(dbName);
        req.onsuccess = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(storeName)) { db.close(); resolve(0); return; }
            const tx = db.transaction(storeName, 'readonly');
            const c = tx.objectStore(storeName).count();
            c.onsuccess = () => { const n = c.result; db.close(); resolve(n); };
            c.onerror = () => { db.close(); resolve(0); };
        };
        req.onerror = () => resolve(0);
    });
}
