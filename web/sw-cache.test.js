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
    let cacheKeys = ['proxion-shell-v1', 'proxion-shell-v235', 'other-cache'];
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

    it('bumps the cache name to v259 (evicts stale caches on upgrade)', () => {
        expect(SRC).toContain('proxion-shell-v259');
        expect(SRC).not.toContain('proxion-shell-v242');
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

    it('never caches a navigation (so never the OIDC callback URL)', async () => {
        const { responded } = await dispatch(sw.listeners, {
            url: ORIGIN + '/?code=abc123&state=xyz', mode: 'navigate',
        });
        expect(responded).toBe(true);
        // The offline shell is the one cached at install; a navigation written
        // into the cache would pair new HTML with old assets.
        expect(sw.puts).toEqual([]);
    });

    it('never intercepts gateway API / websocket paths', async () => {
        for (const url of [ORIGIN + '/api/rooms', ORIGIN + '/ws']) {
            const { responded } = await dispatch(sw.listeners, { url });
            expect(responded, url).toBe(false);
        }
    });
});

// The install step used cache.addAll(SHELL), which is atomic: clientid.jsonld
// 404ing off localhost failed the whole install, so no worker ever activated.
describe('service worker install', () => {
    function loadForInstall(failing) {
        const listeners = {};
        const added = [];
        let skipped = false;
        // The worker passes absolute Requests; record them as shell paths.
        const rel = (r) => (r.url === ORIGIN + '/' ? './' : r.url.slice(ORIGIN.length + 1));
        const cacheModes = [];
        const cache = {
            addAll: async (reqs) => {
                const urls = reqs.map(rel);
                cacheModes.push(...reqs.map((r) => r.cache));
                for (const u of urls) if (failing.has(u)) throw new TypeError('bad response: ' + u);
                added.push(...urls);
            },
            add: async (r) => {
                const u = rel(r);
                cacheModes.push(r.cache);
                if (failing.has(u)) throw new TypeError('bad response: ' + u);
                added.push(u);
            },
        };
        const self = {
            location: new URL(ORIGIN + '/'),
            addEventListener: (type, fn) => { listeners[type] = fn; },
            skipWaiting: () => { skipped = true; },
            clients: { claim: () => {}, matchAll: async () => [] },
            registration: { showNotification: async () => {} },
        };
        new Function('self', 'caches', 'fetch', 'URL', 'Request', 'indexedDB', SRC)(
            self, { open: async () => cache }, async () => ({}), URL, Request, undefined,
        );
        const run = async () => {
            let p = null;
            listeners.install({ waitUntil: (x) => { p = x; } });
            await p;
        };
        return { run, added, cacheModes, skipped: () => skipped };
    }

    it('installs when an optional asset fails, caching everything else', async () => {
        const sw = loadForInstall(new Set(['clientid.jsonld']));
        await sw.run();
        expect(sw.skipped()).toBe(true);
        expect(sw.added).toContain('index.html');
        expect(sw.added).toContain('main.js');
        expect(sw.added).toContain('vendor/jsQR.min.js');
        expect(sw.added).not.toContain('clientid.jsonld');
    });

    it('installs from the network, not the HTTP cache (no stale main.js beside a new page)', async () => {
        const sw = loadForInstall(new Set());
        await sw.run();
        expect(sw.added).toContain('./');
        expect(sw.cacheModes.length).toBeGreaterThan(4);
        expect(new Set(sw.cacheModes)).toEqual(new Set(['reload']));
    });

    it('still fails the install when the page itself cannot be cached', async () => {
        const sw = loadForInstall(new Set(['index.html']));
        await expect(sw.run()).rejects.toThrow(/index\.html/);
        expect(sw.skipped()).toBe(false);
    });
});

// After a deploy the active worker's cache still holds the old scripts. A page
// whose HTML came fresh from the network must not be handed those: it would
// run new HTML with old JS (a removed element id makes old main.js throw).
describe('service worker version consistency', () => {
    function loadWithCache(shellHtml) {
        const listeners = {};
        const store = new Map([[ORIGIN + '/index.html', shellHtml], [ORIGIN + '/main.js', 'js-v1']]);
        const puts = [];
        const fetches = [];
        const key = (r) => (typeof r === 'string' ? new URL(r, ORIGIN + '/').href : r.url);
        const resp = (body) => ({ ok: true, body, clone() { return resp(body); }, text: async () => body });
        const cache = {
            match: async (r) => (store.has(key(r)) ? resp(store.get(key(r))) : undefined),
            put: async (r) => { puts.push(key(r)); },
        };
        const caches = { open: async () => cache, match: (r) => cache.match(r), keys: async () => [], delete: async () => true };
        let network = { '/index.html': 'html-v2', '/': 'html-v2', '/main.js': 'js-v2' };
        const fetchStub = async (req, init) => {
            const u = new URL(key(req));
            fetches.push({ path: u.pathname, cache: init && init.cache });
            return resp(network[u.pathname] ?? '');
        };
        const self = {
            location: new URL(ORIGIN + '/'),
            addEventListener: (type, fn) => { listeners[type] = fn; },
            skipWaiting: () => {}, clients: { claim: () => {}, matchAll: async () => [] },
            registration: { showNotification: async () => {} },
        };
        new Function('self', 'caches', 'fetch', 'URL', 'Request', 'indexedDB', SRC)(
            self, caches, fetchStub, URL, Request, undefined,
        );
        async function get({ path, mode = 'same-origin', clientId = '', resultingClientId = '' }) {
            let p = null;
            listeners.fetch({
                request: { url: ORIGIN + path, method: 'GET', mode },
                clientId, resultingClientId,
                respondWith: (x) => { p = x; },
            });
            const r = await p;
            return r && r.body;
        }
        return { get, puts, fetches, setNetwork: (n) => { network = n; } };
    }

    it('a page whose HTML is newer than the cache gets its scripts from the network', async () => {
        const sw = loadWithCache('html-v1');
        expect(await sw.get({ path: '/', mode: 'navigate', resultingClientId: 'tab1' })).toBe('html-v2');
        expect(await sw.get({ path: '/main.js', clientId: 'tab1' })).toBe('js-v2');
        // Revalidated past the HTTP cache, and the old cache generation is untouched.
        expect(sw.fetches.at(-1)).toEqual({ path: '/main.js', cache: 'no-cache' });
        expect(sw.puts).toEqual([]);
    });

    it('a page matching the cached shell keeps serving scripts from the cache', async () => {
        const sw = loadWithCache('html-v1');
        sw.setNetwork({ '/': 'html-v1', '/main.js': 'js-v2' });
        expect(await sw.get({ path: '/', mode: 'navigate', resultingClientId: 'tab1' })).toBe('html-v1');
        expect(await sw.get({ path: '/main.js', clientId: 'tab1' })).toBe('js-v1');
    });

    it('only the fresh page bypasses the cache, not other open tabs', async () => {
        const sw = loadWithCache('html-v1');
        await sw.get({ path: '/', mode: 'navigate', resultingClientId: 'tab-new' });
        expect(await sw.get({ path: '/main.js', clientId: 'tab-old' })).toBe('js-v1');
    });

    it('offline, a navigation falls back to the cached shell', async () => {
        const sw = loadWithCache('html-v1');
        sw.setNetwork(null);
        // A throwing fetch: network[...] on null throws inside the stub.
        expect(await sw.get({ path: '/', mode: 'navigate', resultingClientId: 'tab1' })).toBe('html-v1');
    });
});

// The page's memory cache reuses a script under its CDN max-age without asking
// the worker again, which would bring back the old script after a deploy.
describe('service worker responses', () => {
    it('serves cached assets marked for revalidation, keeping status and type', async () => {
        const listeners = {};
        const cached = new Response('js-v1', { headers: { 'Cache-Control': 'max-age=600', 'Content-Type': 'text/javascript' } });
        const caches = {
            open: async () => ({ match: async () => undefined, put: async () => {} }),
            match: async () => cached,
        };
        const self = {
            location: new URL(ORIGIN + '/'),
            addEventListener: (type, fn) => { listeners[type] = fn; },
            skipWaiting: () => {}, clients: { claim: () => {}, matchAll: async () => [] },
            registration: { showNotification: async () => {} },
        };
        new Function('self', 'caches', 'fetch', 'URL', 'Request', 'indexedDB', SRC)(
            self, caches, async () => { throw new Error('no network'); }, URL, Request, undefined,
        );
        let p = null;
        listeners.fetch({
            request: { url: ORIGIN + '/main.js', method: 'GET', mode: 'no-cors' },
            clientId: 'tab', respondWith: (x) => { p = x; },
        });
        const r = await p;
        expect(r.status).toBe(200);
        expect(r.headers.get('Cache-Control')).toBe('no-cache');
        expect(r.headers.get('Content-Type')).toBe('text/javascript');
        expect(await r.text()).toBe('js-v1');
    });
});
