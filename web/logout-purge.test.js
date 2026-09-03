// logout-purge.test.js — solidLogout must purge pod-derived state so an account
// switch on a shared device leaks nothing: the SW caches (proxion-shell-*) and
// the offline send queue, in addition to the persisted storage root. Best-effort:
// a failure in either purge must not break the logout.
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
});
