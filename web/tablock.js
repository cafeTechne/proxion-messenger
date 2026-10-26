// One active tab per browser profile.
//
// Every tab on this origin shares the account identity and the DM ratchet state
// in IndexedDB. Two tabs that both send advance the same chain from different
// in-memory copies, the peer can no longer decrypt, and each tab sees the other
// tab's sends as ciphertext. So exactly one tab, the leader, connects to the
// gateway and does ratchet work. Other tabs wait behind a notice with a
// "Use here" button and do not start the app until they become the leader.
//
// The leader holds a Web Lock for its whole lifetime; the browser releases it
// when the tab closes, navigates away or crashes. "Use here" asks the leader
// over a BroadcastChannel to step down: it stops its socket, posts its queued
// (not yet sent) commands to the new tab, releases the lock and reloads into the
// notice, so none of its timers or watchers keep running. A leader that does not
// answer in time (a frozen background tab) is pre-empted with steal: true.
//
// Without navigator.locks (a plain-http LAN address is not a secure context)
// there is no WebCrypto either, so no ratchet to fork: the tab runs on its own.

export const LOCK_NAME = 'proxion-active-tab';
export const CHANNEL_NAME = 'proxion-active-tab';
export const STEAL_AFTER_MS = 3000;

function _randomId() {
    try { return globalThis.crypto.randomUUID(); } catch (_) { return String(Math.random()).slice(2); }
}

export function createTabLock({
    locks = globalThis.navigator?.locks,
    openChannel = (name) => (typeof BroadcastChannel === 'function' ? new BroadcastChannel(name) : null),
    doc = globalThis.document,
    win = globalThis.window,
    stealAfterMs = STEAL_AFTER_MS,
    makeAbortController = () => new AbortController(),
} = {}) {
    const supported = !!(locks && typeof locks.request === 'function');
    const tabId = _randomId();
    let leader = false;
    let stepDownHook = null;   // () => queued payloads; stops the socket
    let steppedDownHook = null; // ({ stolen }) => void; reloads into the notice
    let releaseHeld = null;     // resolves the held lock's callback promise
    let claimResolve = null;    // resolves claim() once we hold the lock
    let takeoverInFlight = null;
    let stealing = false;
    let channel = null;
    let handoffSink = null;
    const handoffStash = [];
    let lastRequester = null;
    const unwatch = [];

    function _becameLeader() {
        leader = true;
        if (claimResolve) { const r = claimResolve; claimResolve = null; r(); }
    }

    // Request the lock. Resolves true once held, false when not granted
    // (ifAvailable and busy, or a queued request that was aborted).
    function _acquire(opts) {
        return new Promise((resolveGranted) => {
            let held = false;
            locks.request(LOCK_NAME, opts, (lock) => {
                if (!lock) { resolveGranted(false); return false; }
                held = true;
                _becameLeader();
                resolveGranted(true);
                return new Promise((res) => { releaseHeld = res; });
            }).catch(() => {
                // Rejected after it was held: another tab stole the lock.
                if (held && leader && !stealing) _stepDown(true);
                resolveGranted(false);
            });
        });
    }

    // The hook stops the socket and waits for in-flight ratchet work, so the
    // lock is only released once the session state on disk is final.
    async function _stepDown(stolen) {
        if (!leader) return;
        leader = false;
        let queued = [];
        try { queued = (stepDownHook && await stepDownHook()) || []; } catch (_) { queued = []; }
        if (channel && queued.length) {
            try { channel.postMessage({ type: 'handoff', to: lastRequester, from: tabId, payloads: queued }); } catch (_) { /* closed */ }
        }
        if (releaseHeld) { const r = releaseHeld; releaseHeld = null; r(); }
        if (steppedDownHook) steppedDownHook({ stolen: !!stolen });
    }

    function _onMessage(ev) {
        const msg = ev && ev.data;
        if (!msg || typeof msg !== 'object' || msg.from === tabId) return;
        if (msg.type === 'release') {
            if (!leader) return;
            lastRequester = msg.from || null;
            _stepDown(false);
        } else if (msg.type === 'handoff') {
            // Only the tab that asked for the takeover sends these, or a stale
            // follower would replay them later.
            const forMe = msg.to ? msg.to === tabId : (leader || !!takeoverInFlight);
            if (!forMe || !Array.isArray(msg.payloads)) return;
            for (const p of msg.payloads) {
                if (handoffSink) handoffSink(p); else handoffStash.push(p);
            }
        }
    }

    function _openChannel() {
        if (channel) return;
        try { channel = openChannel(CHANNEL_NAME); } catch (_) { channel = null; }
        if (channel) channel.onmessage = _onMessage;
    }

    // A follower whose leader went away (tab closed) takes over by itself the
    // next time the user looks at it. It does not queue for the lock: a queued
    // follower would grab it while the leader merely reloads.
    async function tryClaimIfFree() {
        if (!supported || leader || takeoverInFlight) return leader;
        if (doc && doc.visibilityState && doc.visibilityState !== 'visible') return false;
        return _acquire({ ifAvailable: true });
    }

    function _watchForFreeLock() {
        const onShow = () => { tryClaimIfFree(); };
        const add = (target, type) => {
            if (!target || typeof target.addEventListener !== 'function') return;
            target.addEventListener(type, onShow);
            unwatch.push(() => target.removeEventListener(type, onShow));
        };
        add(doc, 'visibilitychange');
        add(win, 'focus');
        add(win, 'pageshow');
    }

    // Leaving for another site (a pod's sign-in page) can park this page in the
    // back/forward cache with the lock still held, and the page that comes back
    // from sign-in then waits behind the notice for a tab that is not there. So
    // the lock goes with pagehide, and a page restored from that cache reloads
    // rather than resume without it.
    let releasedOnHide = false;
    function _watchPageLifecycle() {
        if (!win || typeof win.addEventListener !== 'function') return;
        win.addEventListener('pagehide', () => {
            if (!leader) return;
            leader = false;
            releasedOnHide = true;
            if (releaseHeld) { const r = releaseHeld; releaseHeld = null; r(); }
        });
        win.addEventListener('pageshow', (ev) => {
            if (ev && ev.persisted && releasedOnHide) {
                try { win.location.reload(); } catch (_) { /* no location in tests */ }
            }
        });
    }

    // Resolve once this tab is the leader. onFollower runs (once) when another
    // tab holds the lock, to show the notice.
    async function claim({ onFollower } = {}) {
        if (!supported) { leader = true; return; }
        _openChannel();
        _watchPageLifecycle();
        if (await _acquire({ ifAvailable: true })) return;
        const waiting = new Promise((r) => { claimResolve = r; });
        if (leader) return; // granted in between
        if (onFollower) onFollower();
        _watchForFreeLock();
        await waiting;
        unwatch.splice(0).forEach((fn) => fn());
    }

    // "Use here": ask the leader to hand over, wait for the lock, and steal it
    // if the leader does not let go in time.
    function takeover() {
        if (!supported || leader) return Promise.resolve(true);
        if (takeoverInFlight) return takeoverInFlight;
        _openChannel();
        takeoverInFlight = (async () => {
            const ctrl = makeAbortController();
            if (channel) { try { channel.postMessage({ type: 'release', from: tabId }); } catch (_) { /* closed */ } }
            let timer = null;
            const timedOut = new Promise((res) => { timer = setTimeout(() => res('timeout'), stealAfterMs); });
            const first = await Promise.race([_acquire({ signal: ctrl.signal }), timedOut]);
            clearTimeout(timer);
            if (first === true || leader) return true;
            ctrl.abort();
            if (leader) return true;
            stealing = true;
            try { return await _acquire({ steal: true }); } finally { stealing = false; }
        })();
        takeoverInFlight.finally(() => { takeoverInFlight = null; });
        return takeoverInFlight;
    }

    return {
        supported,
        tabId,
        isLeader: () => leader,
        claim,
        takeover,
        tryClaimIfFree,
        // hook(): stop sending and return (or resolve to) the commands still
        // queued for the gateway.
        onStepDown(hook) { stepDownHook = hook; },
        // hook({ stolen }): runs after the lock is released.
        onSteppedDown(hook) { steppedDownHook = hook; },
        // Commands the previous leader had queued; delivered now and on arrival.
        onHandoff(sink) {
            handoffSink = sink;
            handoffStash.splice(0).forEach((p) => sink(p));
        },
    };
}

// The notice a waiting tab shows. Built in code so it needs no markup in
// index.html; text comes from t() and goes in via textContent.
export function showTabNotice({ doc = globalThis.document, t, onUseHere }) {
    if (!doc || !doc.body) return null;
    let el = doc.getElementById('tab-notice');
    if (el) { el.hidden = false; return el; }
    el = doc.createElement('div');
    el.id = 'tab-notice';
    el.className = 'modal modal--gate tab-notice';
    el.setAttribute('data-modal-persistent', '');
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('aria-labelledby', 'tab-notice-title');
    el.setAttribute('aria-describedby', 'tab-notice-body');
    const box = doc.createElement('div');
    box.className = 'modal__panel modal__panel--sm';
    const title = doc.createElement('h2');
    title.className = 'modal__title';
    title.id = 'tab-notice-title';
    title.textContent = t('tab.otherTitle');
    const body = doc.createElement('p');
    body.id = 'tab-notice-body';
    body.className = 'modal__desc';
    body.textContent = t('tab.otherBody');
    const btn = doc.createElement('button');
    btn.type = 'button';
    btn.id = 'tab-notice-use-here';
    btn.className = 'btn btn--accent btn--lg';
    btn.textContent = t('tab.useHere');
    btn.addEventListener('click', () => {
        btn.disabled = true;
        btn.textContent = t('tab.switching');
        onUseHere();
    });
    const row = doc.createElement('div');
    row.className = 'btn-row modal__footer';
    row.append(btn);
    box.append(title, body, row);
    el.append(box);
    doc.body.append(el);
    try { btn.focus(); } catch (_) { /* no focus in tests */ }
    return el;
}

export function hideTabNotice(doc = globalThis.document) {
    const el = doc && doc.getElementById('tab-notice');
    if (el) el.remove();
}
