// sw-cache.test.js — the service-worker fetch handler must never cache
// cross-origin (authenticated pod) GETs or non-GET requests, must runtime-cache
// only same-origin static assets, and must strip the OIDC query from cached
// navigations. sw.js is a classic worker script (top-level self.addEventListener,
// no exports), so we evaluate it under a mocked ServiceWorkerGlobalScope and
// drive its captured "fetch" listener.
import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const SRC = readFileSync(fileURLToPath(new URL('./sw.js', import.meta.url)), 'utf8');
const ORIGIN = 'https://app.example.com';

// Load sw.js into a fresh mocked scope, returning the captured listeners plus the
// cache mock so a test can inspect what was persisted.
function loadSW() {
    const listeners = {};
    const puts = [];       // { name, key }
    const deleted = [];
    let cacheKeys = ['proxion-shell-v1', 'proxion-shell-v213', 'other-cache'];
    const caches = {
        open: async (name) => ({ put: async (req) => { puts.push({ name, key: req.url }); } }),
        match: async () => undefined,
        keys: async () => cacheKeys.slice(),
        delete: async (k) => { deleted.push(k); return true; },
    };
    const self = {
        location: new URL(ORIGIN + '/'),
        addEventListener: (type, fn) => { listeners[type] = fn; },
        skipWaiting: () => {},
        clients: { claim: () => {}, matchAll: async () => [] },
        registration: { showNotification: async () => {} },
    };
    const fetchStub = async () => ({ ok: true, clone: () => ({}) });
    // eslint-disable-next-line no-new-func
    new Function('self', 'caches', 'fetch', 'URL', 'Request', 'indexedDB', SRC)(
        self, caches, fetchStub, URL, Request, undefined,
    );
    return { listeners, puts, deleted };
}

// Build a fake FetchEvent and run the handler; returns whether respondWith was
// called and (awaited) the response promise so caching side effects settle.
async function dispatch(listeners, { url, method = 'GET', mode = 'same-origin' }) {
    let promise = null;
    const event = {
        request: { url, method, mode, clone: () => ({}) },
        respondWith: (p) => { promise = p; },
    };
    listeners.fetch(event);
    const responded = promise !== null;
    if (responded) { try { await promise; } catch { /* fallback path */ } }
    // Let the un-awaited caches.open(...).then(put) microtasks flush.
    await Promise.resolve();
    await Promise.resolve();
    return { responded };
}

describe('service worker fetch handler', () => {
    let sw;
    beforeEach(() => { sw = loadSW(); });

    it('bumps the cache name to v213 (evicts poisoned caches on upgrade)', () => {
        expect(SRC).toContain('proxion-shell-v213');
        expect(SRC).not.toContain('proxion-shell-v212');
    });

    it('bypasses a cross-origin GET (pod fetch): not intercepted, not cached', async () => {
        const { responded } = await dispatch(sw.listeners, {
            url: 'https://alice.pod.example/proxion/rooms/general.ttl',
        });
        expect(responded).toBe(false);   // straight to network, no respondWith
        expect(sw.puts).toEqual([]);      // nothing persisted
    });

    it('bypasses a non-GET same-origin request', async () => {
        const { responded } = await dispatch(sw.listeners, {
            url: ORIGIN + '/main.js', method: 'POST',
        });
        expect(responded).toBe(false);
        expect(sw.puts).toEqual([]);
    });

    it('caches a same-origin static shell GET', async () => {
        const { responded } = await dispatch(sw.listeners, { url: ORIGIN + '/main.js' });
        expect(responded).toBe(true);
        expect(sw.puts.map((p) => p.key)).toEqual([ORIGIN + '/main.js']);
    });

    it('does not cache a same-origin non-static GET (unbounded growth guard)', async () => {
        const { responded } = await dispatch(sw.listeners, { url: ORIGIN + '/some/dynamic/path' });
        expect(responded).toBe(true);   // still served, just not persisted
        expect(sw.puts).toEqual([]);
    });

    it('strips the OIDC query when caching a navigation', async () => {
        const { responded } = await dispatch(sw.listeners, {
            url: ORIGIN + '/?code=abc123&state=xyz', mode: 'navigate',
        });
        expect(responded).toBe(true);
        // Cached under the bare path — never the callback URL with its code/state.
        expect(sw.puts.map((p) => p.key)).toEqual([ORIGIN + '/']);
    });

    it('never intercepts gateway API / websocket paths', async () => {
        for (const url of [ORIGIN + '/api/rooms', ORIGIN + '/ws']) {
            const { responded } = await dispatch(sw.listeners, { url });
            expect(responded, url).toBe(false);
        }
    });
});
