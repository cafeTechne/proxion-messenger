// tablock.test.js: one active tab per browser profile. Two tabs that both run
// the DM ratchet fork it (the peer gets "could not decrypt"), so only the Web
// Lock holder may connect; others wait and can take over cleanly.
import { describe, it, expect, vi } from 'vitest';
import { createTabLock, showTabNotice, LOCK_NAME } from './tablock.js';
import { createConnection } from './connection.js';

// A LockManager with the semantics the module relies on: exclusive, FIFO queue,
// ifAvailable, signal (abort a queued request), steal, released when the
// callback's promise settles or the "tab" crashes.
function makeLockManager() {
    let holder = null;            // { release, reject }
    const queue = [];
    function grant(entry) {
        let release;
        const done = new Promise((r) => { release = r; });
        holder = { entry, release };
        Promise.resolve(entry.cb({ name: LOCK_NAME })).then((v) => {
            if (holder && holder.entry === entry) { holder = null; entry.resolve(v); next(); }
        }, (e) => { if (holder && holder.entry === entry) { holder = null; entry.reject(e); next(); } });
        return done;
    }
    function next() {
        if (holder || !queue.length) return;
        grant(queue.shift());
    }
    return {
        get held() { return !!holder; },
        crash(entry) { if (holder && holder.entry === entry) { holder = null; next(); } },
        crashHolder() { holder = null; next(); },
        request(name, opts, cb) {
            return new Promise((resolve, reject) => {
                const entry = { cb, resolve, reject };
                setTimeout(() => {
                    if (opts.steal) {
                        if (holder) { const old = holder.entry; holder = null; old.reject(new DOMException('stolen', 'AbortError')); }
                        grant(entry);
                        return;
                    }
                    if (!holder && !queue.length) { grant(entry); return; }
                    if (opts.ifAvailable) { Promise.resolve(cb(null)).then(resolve, reject); return; }
                    if (opts.signal) {
                        opts.signal.addEventListener('abort', () => {
                            const i = queue.indexOf(entry);
                            if (i >= 0) { queue.splice(i, 1); reject(new DOMException('aborted', 'AbortError')); }
                        });
                    }
                    queue.push(entry);
                }, 0);
            });
        },
    };
}

// A BroadcastChannel bus: messages go to every other channel of the same name.
function makeBus() {
    const chans = new Set();
    return (name) => {
        const ch = {
            name, onmessage: null,
            postMessage(data) {
                for (const o of chans) if (o !== ch && o.name === name) setTimeout(() => o.onmessage && o.onmessage({ data }), 0);
            },
            close() { chans.delete(ch); },
        };
        chans.add(ch);
        return ch;
    };
}

const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));

function tab(locks, openChannel, extra = {}) {
    return createTabLock({ locks, openChannel, doc: null, win: null, stealAfterMs: 200, ...extra });
}

describe('tab lock', () => {
    it('the first tab leads; a second waits behind the notice', async () => {
        const locks = makeLockManager(); const bus = makeBus();
        const a = tab(locks, bus); const b = tab(locks, bus);
        await a.claim();
        expect(a.isLeader()).toBe(true);
        const onFollower = vi.fn();
        let bReady = false;
        b.claim({ onFollower }).then(() => { bReady = true; });
        await tick(20);
        expect(onFollower).toHaveBeenCalledTimes(1);
        expect(bReady).toBe(false);
        expect(b.isLeader()).toBe(false);
    });

    it('Use here: the leader stops, hands over unsent commands, then releases', async () => {
        const locks = makeLockManager(); const bus = makeBus();
        const a = tab(locks, bus); const b = tab(locks, bus);
        await a.claim();
        const order = [];
        a.onStepDown(async () => { order.push('stop'); await tick(10); order.push('idle'); return [{ cmd: 'send_dm', n: 1 }]; });
        a.onSteppedDown(({ stolen }) => order.push(stolen ? 'stolen' : 'released'));
        const bReady = b.claim();
        await tick(10);
        await b.takeover();
        await bReady;
        expect(b.isLeader()).toBe(true);
        expect(a.isLeader()).toBe(false);
        // Lock released only after in-flight work finished.
        expect(order).toEqual(['stop', 'idle', 'released']);
        const got = [];
        b.onHandoff((p) => got.push(p));
        await tick(10);
        expect(got).toEqual([{ cmd: 'send_dm', n: 1 }]);
    });

    it('a leader that never answers is pre-empted with steal', async () => {
        const locks = makeLockManager(); const bus = makeBus();
        const a = tab(locks, () => null);   // frozen: never hears the release request
        const b = tab(locks, bus);
        await a.claim();
        const stepped = vi.fn();
        a.onStepDown(() => []);
        a.onSteppedDown(stepped);
        b.claim();
        await tick(10);
        expect(await b.takeover()).toBe(true);
        await tick(10);
        expect(b.isLeader()).toBe(true);
        expect(a.isLeader()).toBe(false);
        expect(stepped).toHaveBeenCalledWith({ stolen: true });
    });

    it('never two leaders at once across a takeover', async () => {
        const locks = makeLockManager(); const bus = makeBus();
        const a = tab(locks, bus); const b = tab(locks, bus);
        await a.claim();
        let overlap = false;
        const watch = setInterval(() => { if (a.isLeader() && b.isLeader()) overlap = true; }, 1);
        a.onStepDown(async () => { await tick(30); return []; });
        b.claim();
        await tick(5);
        await b.takeover();
        clearInterval(watch);
        expect(overlap).toBe(false);
        expect(b.isLeader()).toBe(true);
    });

    it('a crashed leader frees the lock; the waiting tab claims it when shown', async () => {
        const locks = makeLockManager(); const bus = makeBus();
        const a = tab(locks, bus); const b = tab(locks, bus);
        await a.claim();
        let bReady = false;
        b.claim().then(() => { bReady = true; });
        await tick(10);
        locks.crashHolder();             // browser drops the crashed tab's lock
        expect(await b.tryClaimIfFree()).toBe(true);
        await tick(0);
        expect(bReady).toBe(true);
    });

    it('a waiting tab does not queue for the lock (a leader reload keeps it)', async () => {
        const locks = makeLockManager(); const bus = makeBus();
        const a = tab(locks, bus); const b = tab(locks, bus);
        await a.claim();
        b.claim();
        await tick(10);
        locks.crashHolder();             // the leader tab reloads
        const a2 = tab(locks, bus);      // ...and its new page claims first
        await a2.claim();
        expect(a2.isLeader()).toBe(true);
        expect(b.isLeader()).toBe(false);
    });

    it('a hidden tab does not grab a free lock', async () => {
        const locks = makeLockManager(); const bus = makeBus();
        const doc = { visibilityState: 'hidden', addEventListener() {}, removeEventListener() {} };
        const a = tab(locks, bus); const b = tab(locks, bus, { doc });
        await a.claim();
        b.claim();
        await tick(10);
        locks.crashHolder();
        expect(await b.tryClaimIfFree()).toBe(false);
        expect(locks.held).toBe(false);
    });

    it('handoff is only accepted by the tab that asked for it', async () => {
        const locks = makeLockManager(); const bus = makeBus();
        const a = tab(locks, bus); const b = tab(locks, bus); const c = tab(locks, bus);
        await a.claim();
        a.onStepDown(() => [{ cmd: 'x' }]);
        a.onSteppedDown(() => {});
        b.claim(); c.claim();
        await tick(10);
        await b.takeover();
        const gotC = [];
        c.onHandoff((p) => gotC.push(p));
        await tick(10);
        expect(gotC).toEqual([]);
    });

    it('a page parked in the back/forward cache lets go, so sign-in can come back', async () => {
        const locks = makeLockManager(); const bus = makeBus();
        const handlers = {};
        const reload = vi.fn();
        const win = { addEventListener: (type, fn) => { handlers[type] = fn; }, location: { reload } };
        const a = tab(locks, bus, { win });
        await a.claim();
        handlers.pagehide({ persisted: true });
        await tick(5);
        expect(a.isLeader()).toBe(false);
        expect(locks.held).toBe(false);
        const b = tab(locks, bus);
        const onFollower = vi.fn();
        await b.claim({ onFollower });
        expect(b.isLeader()).toBe(true);
        expect(onFollower).not.toHaveBeenCalled();
        handlers.pageshow({ persisted: true });
        expect(reload).toHaveBeenCalled();
    });

    it('without navigator.locks the tab runs on its own', async () => {
        const t1 = createTabLock({ locks: undefined, doc: null, win: null });
        await t1.claim();
        expect(t1.supported).toBe(false);
        expect(t1.isLeader()).toBe(true);
    });
});

describe('connection gated on the tab lock', () => {
    function stubDom() {
        const el = () => ({ style: {}, className: '', textContent: '', innerText: '', addEventListener() {} });
        globalThis.document = { getElementById: () => el(), querySelector: () => el() };
        globalThis.localStorage = { getItem: () => null, setItem() {} };
    }
    it('a waiting tab never opens a socket, even when something queues a send', () => {
        stubDom();
        const opened = [];
        globalThis.WebSocket = class { constructor(u) { opened.push(u); this.readyState = 0; } close() {} };
        globalThis.WebSocket.OPEN = 1; globalThis.WebSocket.CLOSED = 3; globalThis.WebSocket.CONNECTING = 0;
        let socket = null;
        const conn = createConnection({
            wsUrl: 'ws://x', getSocket: () => socket, setSocket: (s) => { socket = s; },
            getClientDid: () => 'did:key:z', generateOrLoadIdentity: async () => {}, handleEventAsync: () => {},
            isActive: () => false,
        });
        conn.connect();
        conn.socketSendOrQueue({ cmd: 'send_dm' });
        expect(opened).toEqual([]);
    });

    it('shutdown closes the socket for good and returns the unsent queue', () => {
        stubDom();
        const opened = [];
        globalThis.WebSocket = class { constructor(u) { opened.push(this); this.readyState = 0; } close() { this.closed = true; } };
        globalThis.WebSocket.OPEN = 1; globalThis.WebSocket.CLOSED = 3; globalThis.WebSocket.CONNECTING = 0;
        let socket = null;
        const conn = createConnection({
            wsUrl: 'ws://x', getSocket: () => socket, setSocket: (s) => { socket = s; },
            getClientDid: () => 'did:key:z', generateOrLoadIdentity: async () => {}, handleEventAsync: () => {},
        });
        conn.connect();
        conn.socketSendOrQueue({ cmd: 'send_dm', n: 1 });
        const ws = opened[0];
        const queued = conn.shutdown();
        expect(queued).toEqual([{ cmd: 'send_dm', n: 1 }]);
        expect(ws.closed).toBe(true);
        expect(socket).toBe(null);
        ws.onclose && ws.onclose();      // the disowned socket's close schedules nothing
        conn.connect();
        expect(opened.length).toBe(1);
    });
});

describe('tab notice', () => {
    it('shows a dialog whose button takes over', () => {
        const made = [];
        const mk = (tag) => {
            const e = { tag, children: [], attrs: {}, listeners: {}, setAttribute(k, v) { this.attrs[k] = v; },
                append(...c) { this.children.push(...c); }, addEventListener(t, f) { this.listeners[t] = f; }, focus() {} };
            made.push(e); return e;
        };
        const doc = { body: mk('body'), getElementById: () => null, createElement: mk };
        const onUseHere = vi.fn();
        const el = showTabNotice({ doc, t: (k) => k, onUseHere });
        expect(el.attrs.role).toBe('dialog');
        const btn = made.find((e) => e.id === 'tab-notice-use-here');
        expect(btn.textContent).toBe('tab.useHere');
        btn.listeners.click();
        expect(onUseHere).toHaveBeenCalled();
        expect(btn.disabled).toBe(true);
    });
});
