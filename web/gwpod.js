// gwpod.js: the desktop client's pod I/O, through the gateway's pod bridge.
//
// The desktop app has no Solid session in the browser: its gateway holds the
// pod credentials. To talk to browser-build contacts it still runs the browser
// build's protocol code (webdm.js, webpresence.js) over podio.js, with a fetch
// that goes over the WebSocket as pod_fetch and comes back as a Response-like
// object. The gateway decides which URLs that fetch may touch and never sees
// plaintext: envelopes are ratchet-encrypted and signed here.
//
//   const bridge = createGwPodBridge({ send })
//   bridge.handleEvent(ev)        feed gateway events; returns true when consumed
//   bridge.requestInfo()          ask for pod_bridge (ready/webid/pod_root/reason)
//   bridge.io                     a podio instance (podEnsureDmInbox, podDropDm, ...)
//   bridge.fetch(url, opts)       Response-like, rejects on timeout like a network error
//   bridge.watchResource(url, cb, { fast })  pod_watch; returns unsubscribe
//   bridge.reset()                the socket dropped: fail pending requests, keep watches

import { createPodIo } from './podio.js';

export const POD_BRIDGE_EVENTS = new Set(['pod_bridge', 'pod_fetch_result', 'pod_changed', 'pod_watch_refused']);

// A fetch-shaped response over a pod_fetch_result.
export function responseFromResult(r) {
    const status = Number(r && r.status) || 0;
    const headers = {};
    for (const [k, v] of Object.entries((r && r.headers) || {})) headers[String(k).toLowerCase()] = v;
    const body = typeof (r && r.body) === 'string' ? r.body : '';
    return {
        ok: status >= 200 && status < 300,
        status,
        error: (r && r.error) || null,
        headers: { get: (k) => (headers[String(k).toLowerCase()] ?? null), has: (k) => String(k).toLowerCase() in headers },
        text: async () => body,
        json: async () => JSON.parse(body),
    };
}

export function createGwPodBridge({ send, timeoutMs = 15000, onInfo = null, onWatchRefused = null }) {
    let _info = { ready: false, reason: 'unknown' };
    let _seq = 0;
    const _pending = new Map();   // req_id -> { resolve, reject, timer }
    const _watches = new Map();   // url -> { cbs: Set, fast: boolean }

    function fetch(url, opts = {}) {
        const req_id = 'pf' + (++_seq) + '-' + Math.random().toString(36).slice(2, 8);
        const headers = {};
        const h = opts.headers || {};
        for (const [k, v] of Object.entries(h)) if (typeof v === 'string') headers[k] = v;
        const body = typeof opts.body === 'string' ? opts.body : (opts.body == null ? null : String(opts.body));
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => {
                _pending.delete(req_id);
                reject(new TypeError('pod bridge request timed out'));
            }, timeoutMs);
            _pending.set(req_id, { resolve, reject, timer });
            try {
                send({ cmd: 'pod_fetch', req_id, method: (opts.method || 'GET').toUpperCase(), url, headers, body });
            } catch (err) {
                clearTimeout(timer);
                _pending.delete(req_id);
                reject(new TypeError('pod bridge unavailable'));
            }
        });
    }

    function _sendWatch(url, fast) {
        try { send({ cmd: 'pod_watch', url, fast: !!fast }); } catch { /* resent on the next resubscribe */ }
    }

    function watchResource(url, cb, { fast = false } = {}) {
        let w = _watches.get(url);
        if (!w) { w = { cbs: new Set(), fast: !!fast }; _watches.set(url, w); _sendWatch(url, fast); }
        else if (fast && !w.fast) { w.fast = true; _sendWatch(url, true); }
        w.cbs.add(cb);
        return () => {
            const cur = _watches.get(url);
            if (!cur) return;
            cur.cbs.delete(cb);
            if (!cur.cbs.size) {
                _watches.delete(url);
                try { send({ cmd: 'pod_unwatch', url }); } catch { /* socket gone; the gateway forgets it */ }
            }
        };
    }

    // Re-arm every watch on a fresh connection (the gateway forgets a closed socket's).
    function resubscribe() {
        for (const [url, w] of _watches) _sendWatch(url, w.fast);
    }

    function reset() {
        for (const [, p] of _pending) { clearTimeout(p.timer); p.reject(new TypeError('pod bridge connection lost')); }
        _pending.clear();
        _info = { ready: false, reason: 'disconnected' };
    }

    function handleEvent(ev) {
        if (!ev || !POD_BRIDGE_EVENTS.has(ev.type)) return false;
        if (ev.type === 'pod_bridge') {
            _info = {
                ready: !!ev.ready, webid: ev.webid || null, pod_root: ev.pod_root || null,
                auth: ev.auth || null, reason: ev.reason || '', consumer: !!ev.consumer,
            };
            if (onInfo) { try { onInfo(_info); } catch { /* listener errors stay local */ } }
        } else if (ev.type === 'pod_fetch_result') {
            const p = _pending.get(ev.req_id);
            if (p) { clearTimeout(p.timer); _pending.delete(ev.req_id); p.resolve(responseFromResult(ev)); }
        } else if (ev.type === 'pod_changed') {
            const w = _watches.get(ev.url);
            if (w) for (const cb of [...w.cbs]) { try { cb(ev.url); } catch { /* one watcher's error */ } }
        } else if (ev.type === 'pod_watch_refused') {
            if (onWatchRefused) { try { onWatchRefused(ev); } catch { /* ignore */ } }
        }
        return true;
    }

    const io = createPodIo({
        fetch,
        isLoggedIn: () => !!_info.ready,
        storageRoot: () => (_info.ready ? _info.pod_root : null),
        webId: () => (_info.ready ? _info.webid : null),
    });

    return {
        io, fetch, watchResource, resubscribe, reset, handleEvent,
        info: () => _info,
        requestInfo: () => { try { send({ cmd: 'pod_bridge_info' }); } catch { /* not connected */ } },
        _pendingCount: () => _pending.size,
    };
}
