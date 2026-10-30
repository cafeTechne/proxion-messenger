// podhealth.js: is the browser build's pod session still usable?
//
// In the browser build the pod is the transport, so a pod the app can no longer
// reach or write to means nothing is sent or received. The Solid session can
// lapse without telling the app: a token refresh that fails on a network error
// (a laptop waking before Wi-Fi is back) is never retried, so every pod call
// answers 401 from then on, and a refresh the identity provider rejects logs
// the session out in memory and emits `sessionExpired`.
//
// This watches the session's events and every response from the user's own pod
// (by wrapping session.fetch) and reports one state:
//   'ok'           pod calls work
//   'offline'      the browser has no network
//   'unreachable'  the pod does not answer (network errors, 5xx)
//   'expired'      the session ended; the user must sign in again
// onChange(state) fires on every change. onRecovered() fires when a pod call
// succeeds after one failed, so queued writes and undelivered sends can go out.

const RETRY_MIN_MS = 5000;
const RETRY_MAX_MS = 60000;

export function createPodHealth({
    session, ownOrigins = () => [], probeUrl = () => null,
    onChange, onRecovered, win = (typeof window !== 'undefined' ? window : null),
    failLimit = 2,
} = {}) {
    let expired = null;          // null | 'event' (session ended) | 'token' (pod answers 401)
    let netFails = 0;            // consecutive network errors / 5xx from our pod
    let authFails = 0;           // consecutive 401s from our pod while signed in
    let failedSince = false;     // a pod call failed since the last success
    let signedIn = false;        // the app finished signing in this page load
    let offline = !!(win && win.navigator && win.navigator.onLine === false);
    let last = 'ok';
    let retryTimer = null;
    let retryDelay = RETRY_MIN_MS;

    function state() {
        if (expired) return 'expired';
        if (offline) return 'offline';
        if (netFails >= failLimit) return 'unreachable';
        return 'ok';
    }

    function _emit() {
        const s = state();
        if (s !== last) {
            last = s;
            if (onChange) { try { onChange(s); } catch (_) { /* UI only */ } }
        }
        _scheduleProbe();
    }

    // While the pod is unreachable or answers 401, check it again with backoff:
    // a pod that comes back with the network up fires no `online` event.
    function _scheduleProbe() {
        const s = state();
        const wanted = s === 'unreachable' || (s === 'expired' && expired === 'token');
        if (!wanted) {
            if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
            retryDelay = RETRY_MIN_MS;
            return;
        }
        if (retryTimer) return;
        retryTimer = setTimeout(() => {
            retryTimer = null;
            retryDelay = Math.min(retryDelay * 2, RETRY_MAX_MS);
            probe();
        }, retryDelay);
    }

    function _origin(u) {
        try { return new URL(typeof u === 'string' ? u : (u && u.url) || '').origin; } catch { return null; }
    }

    function _isOwn(url) {
        const o = _origin(url);
        if (!o) return false;
        let mine = [];
        try { mine = (ownOrigins() || []).map(_origin).filter(Boolean); } catch { mine = []; }
        return mine.includes(o);
    }

    function _recovered() {
        if (onRecovered) { try { onRecovered(); } catch (_) { /* best-effort */ } }
    }

    function _success() {
        const hadFailure = failedSince;
        netFails = 0;
        authFails = 0;
        failedSince = false;
        offline = false;   // a response proves the network is up
        if (expired === 'token' && session.info.isLoggedIn) expired = null;
        _emit();
        if (hadFailure && state() === 'ok') _recovered();
    }

    function _networkFailure() {
        netFails++;
        failedSince = true;
        _emit();
    }

    function _observe(status) {
        if (status === 401) {
            if (!session.info.isLoggedIn) return;   // signed out: an anonymous read was refused
            authFails++;
            failedSince = true;
            if (authFails >= failLimit && !expired) expired = 'token';
            _emit();
            return;
        }
        if (status >= 500) { _networkFailure(); return; }
        _success();
    }

    // Route every session.fetch through the observer. pod.js and the web modules
    // call solidSession.fetch at call time, so this covers all of them.
    function wrapFetch() {
        const orig = session.fetch;
        if (typeof orig !== 'function' || orig._podHealth) return;
        const wrapped = async (url, init) => {
            const own = _isOwn(url);
            let res;
            try {
                res = await orig.call(session, url, init);
            } catch (err) {
                if (own && !(err && err.name === 'AbortError')) _networkFailure();
                throw err;
            }
            if (own && res && typeof res.status === 'number') _observe(res.status);
            return res;
        };
        wrapped._podHealth = true;
        session.fetch = wrapped;
    }

    // Ask our pod whether it answers now; the wrapped fetch records the result.
    function probe() {
        if (offline || expired === 'event') return;
        let url = null;
        try { url = probeUrl(); } catch { url = null; }
        if (!url) return;
        Promise.resolve()
            .then(() => session.fetch(url, { method: 'HEAD', cache: 'no-store' }))
            .catch(() => {});
    }

    // The app finished signing in (or signed in again).
    function markSignedIn() {
        const wasDown = !!expired || failedSince;
        signedIn = true;
        expired = null;
        authFails = 0;
        _emit();
        if (wasDown && state() === 'ok') _recovered();
    }

    function _sessionEnded() {
        if (!signedIn) return;   // a sign-in error, not a lapse of a live session
        expired = 'event';
        _emit();
    }

    function start() {
        wrapFetch();
        const ev = session.events;
        if (ev && typeof ev.on === 'function') {
            ev.on('sessionExpired', _sessionEnded);
            // A failed refresh also emits `error`, and the library then logs the
            // session out in memory without a sessionExpired in every case.
            ev.on('error', _sessionEnded);
        }
        if (win && typeof win.addEventListener === 'function') {
            win.addEventListener('offline', () => { offline = true; _emit(); });
            win.addEventListener('online', () => {
                const was = state();
                offline = false;
                netFails = 0;
                _emit();
                probe();
                if (was === 'offline' && state() === 'ok') _recovered();
            });
        }
        const doc = win && win.document;
        if (doc && typeof doc.addEventListener === 'function') {
            doc.addEventListener('visibilitychange', () => {
                if (doc.visibilityState === 'visible' && state() !== 'ok') probe();
            });
        }
    }

    return { start, state, probe, markSignedIn, wrapFetch, _observe };
}
