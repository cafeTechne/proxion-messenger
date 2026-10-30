import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('./e2e.js', () => ({
  myX25519PubB64u: () => 'MYX25519PUB',
}));

import { createConnection, normalizeGatewayUrl, planThreadCatchUp, createRateRetry, createSendPacer, queueKey, PACE_LIMIT, catchUpUnread } from './connection.js';

let els, store, made, hostSocket;
function mkEl(over = {}) {
  return { style: {}, className: '', innerText: '', innerHTML: '', textContent: '', ...over };
}
class FakeWS {
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
  constructor(url) { this.url = url; this.readyState = FakeWS.CONNECTING; this.sent = []; made.push(this); }
  send(m) { this.sent.push(JSON.parse(m)); }
  close() { this.readyState = FakeWS.CLOSED; if (this.onclose) this.onclose(); }
}
beforeEach(() => {
  els = { username: mkEl(), 'conn-banner': mkEl(), 'message-feed': mkEl(), dot: mkEl() };
  store = {};
  made = [];
  hostSocket = null;
  global.WebSocket = FakeWS;
  global.document = {
    getElementById: (id) => (id in els ? els[id] : (els[id] = mkEl())),
    querySelector: (sel) => (sel === '.dot' ? els.dot : mkEl()),
  };
  global.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  };
  vi.useFakeTimers();
});
// Connections from earlier tests share hostSocket: stop them for good (a
// stopped connection never reconnects, whatever timer or event fires later)
// and drop their timers, so no test depends on which ones ran before it.
let conns = [];
afterEach(() => {
  for (const c of conns) c.shutdown();
  conns = [];
  vi.clearAllTimers();
  vi.useRealTimers();
});

function make(over = {}) {
  const c = createConnection({
    wsUrl: 'ws://test/gw',
    getSocket: () => hostSocket,
    setSocket: (s) => { hostSocket = s; },
    getClientDid: () => 'did:key:zSelf',
    generateOrLoadIdentity: async () => {},
    handleEventAsync: vi.fn(),
    ...over,
  });
  conns.push(c);
  return c;
}

describe('connect', () => {
  it('opens a socket at wsUrl and stores it via setSocket', () => {
    make().connect();
    expect(made).toHaveLength(1);
    expect(made[0].url).toBe('ws://test/gw');
    expect(hostSocket).toBe(made[0]);
  });
  it('on open: registers, but defers queued cmds until flushPending (post-register)', async () => {
    const c = make();
    c.connect();
    const ws = made[0];
    // queue a command while still connecting
    c.socketSendOrQueue({ cmd: 'queued_thing' });
    ws.readyState = FakeWS.OPEN;
    await ws.onopen();
    const register = ws.sent.find(m => m.cmd === 'register');
    expect(register).toMatchObject({ did: 'did:key:zSelf', x25519_pub: 'MYX25519PUB' });
    // Queued command must NOT be sent yet — it would hit the gateway before we're
    // registered and be dropped as "Not registered".
    expect(ws.sent.find(m => m.cmd === 'queued_thing')).toBeFalsy();
    // Once registration is confirmed, flushPending delivers it.
    c.flushPending();
    expect(ws.sent.find(m => m.cmd === 'queued_thing')).toBeTruthy();
  });
  it('on message: delegates parsed data to handleEventAsync', () => {
    const handleEventAsync = vi.fn();
    const c = make({ handleEventAsync });
    c.connect();
    const ws = made[0];
    hostSocket = ws;
    ws.onmessage({ data: JSON.stringify({ type: 'hello', n: 1 }) });
    expect(handleEventAsync).toHaveBeenCalledWith({ type: 'hello', n: 1 });
  });
  it('ignores messages from a superseded socket', () => {
    const handleEventAsync = vi.fn();
    const c = make({ handleEventAsync });
    c.connect();
    const ws = made[0];
    hostSocket = { other: true }; // socket was replaced
    ws.onmessage({ data: JSON.stringify({ type: 'stale' }) });
    expect(handleEventAsync).not.toHaveBeenCalled();
  });
});

describe('socketSendOrQueue', () => {
  it('sends immediately when the socket is open', () => {
    const c = make();
    hostSocket = new FakeWS('x'); hostSocket.readyState = FakeWS.OPEN;
    c.socketSendOrQueue({ cmd: 'now' });
    expect(hostSocket.sent).toEqual([{ cmd: 'now' }]);
  });
  it('kicks off a fresh connect when the socket is closed', () => {
    const c = make();
    hostSocket = new FakeWS('x'); hostSocket.readyState = FakeWS.CLOSED;
    c.socketSendOrQueue({ cmd: 'later' });
    // a brand-new socket was created by connect()
    expect(made.length).toBeGreaterThanOrEqual(1);
  });
  it('caps the pending queue at 200 (drop-oldest)', () => {
    const c = make();
    hostSocket = new FakeWS('x'); hostSocket.readyState = FakeWS.CONNECTING;
    for (let i = 0; i < 250; i++) c.socketSendOrQueue({ cmd: 'q', i });
    expect(c.state._pendingOnConnect.length).toBe(200);
    // Oldest were dropped; the newest survive.
    expect(c.state._pendingOnConnect[0].payload.i).toBe(50);
    expect(c.state._pendingOnConnect[199].payload.i).toBe(249);
  });
});

describe('reconnect banner', () => {
  function withRetryButton() {
    const handlers = {};
    els['conn-retry-btn'] = mkEl({ addEventListener: (type, fn) => { handlers[type] = fn; } });
    return handlers;
  }
  // Drive one failed attempt on the current socket.
  function failCurrent() { hostSocket.readyState = FakeWS.CLOSED; hostSocket.onclose(); }
  // connect(), drop once (instant retry), then fail the retry: the second
  // failure schedules a 6s backoff and shows the banner.
  function startOffline(c) {
    c.connect();
    failCurrent();
    expect(els['conn-banner'].style.display).toBe('none'); // instant retry, no banner
    vi.advanceTimersByTime(0);
    failCurrent();
  }

  it('shows the offline copy with a countdown and an offline sidebar label', () => {
    withRetryButton();
    startOffline(make());
    expect(els['conn-banner'].style.display).toBe('flex');
    expect(els['conn-banner-text'].textContent).toBe('conn.unreachable');
    expect(els.username.innerText).toBe('conn.offline');
  });

  it('asks whether the app is running after three failures in a row', () => {
    withRetryButton();
    startOffline(make());
    expect(els['conn-banner-text'].textContent).not.toContain('conn.isAppRunning');
    vi.advanceTimersByTime(6000); // backoff connect fires
    failCurrent();
    expect(els['conn-banner-text'].textContent).toContain('conn.isAppRunning');
  });

  it('"Retry now" reconnects at once and cancels the scheduled attempt', () => {
    const handlers = withRetryButton();
    const c = make();
    startOffline(c);
    const before = made.length;
    handlers.click();
    expect(made.length).toBe(before + 1);           // fresh socket right away
    const fresh = hostSocket;
    vi.advanceTimersByTime(7000);                   // the old 6s backoff must not fire
    expect(hostSocket).toBe(fresh);
    expect(c.state._retryTimeout).toBe(null);
  });

  it('a successful connect resets the failure count and hides the banner', async () => {
    withRetryButton();
    const c = make();
    startOffline(c);
    vi.advanceTimersByTime(6000);
    const ws = hostSocket;
    ws.readyState = FakeWS.OPEN;
    await ws.onopen();
    expect(c.state._failures).toBe(0);
    expect(els['conn-banner'].style.display).toBe('none');
    expect(els.username.innerText).toBe('conn.online');
  });
});

describe('unlinked device', () => {
  it('stops reconnecting, drops the delegation cert and says why', () => {
    store.proxion_delegation_cert = '{"id":"c1"}';
    global.localStorage.removeItem = (k) => { delete store[k]; };
    make().connect();
    const ws = made[0];
    ws.readyState = FakeWS.CLOSED;
    ws.onclose({ code: 1008, reason: 'identity_revoked' });
    vi.advanceTimersByTime(60000);
    expect(made).toHaveLength(1);
    expect(store.proxion_delegation_cert).toBeUndefined();
    expect(els['conn-banner-text'].textContent).toMatch(/deviceUnlinked|unlinked/);
  });
});

describe('forceReconnect', () => {
  it('no-ops when the socket is already open', () => {
    const c = make();
    hostSocket = new FakeWS('x'); hostSocket.readyState = FakeWS.OPEN;
    made = []; // ignore the setup socket
    c.forceReconnect();
    expect(made).toHaveLength(0); // no new socket constructed
  });
  it('disowns the old socket and connects fresh when not open', () => {
    const c = make();
    const old = new FakeWS('x'); old.readyState = FakeWS.CLOSED;
    hostSocket = old;
    made = []; // ignore the setup socket
    c.forceReconnect();
    expect(made).toHaveLength(1); // a new socket
    expect(hostSocket).toBe(made[0]);
  });
});

describe('dead sockets', () => {
  it('drop their handlers so each reconnect does not leave four more behind', () => {
    const c = make();
    c.connect();
    const first = made[0];
    first.readyState = FakeWS.OPEN;
    first.close();             // drop: onclose schedules an instant retry
    vi.advanceTimersByTime(1);
    const second = made[1];
    expect(second).toBeTruthy();
    for (const h of ['onopen', 'onmessage', 'onerror', 'onclose']) expect(first[h]).toBeNull();
    c.forceReconnect();        // superseded while connecting
    for (const h of ['onopen', 'onmessage', 'onerror', 'onclose']) expect(second[h]).toBeNull();
    expect(hostSocket).toBe(made[2]);
  });
});

describe('normalizeGatewayUrl', () => {
  it('keeps ws and wss addresses', () => {
    expect(normalizeGatewayUrl('ws://127.0.0.1:7474')).toBe('ws://127.0.0.1:7474');
    expect(normalizeGatewayUrl(' wss://chat.example.com ')).toBe('wss://chat.example.com');
    expect(normalizeGatewayUrl('wss://chat.example.com/ws')).toBe('wss://chat.example.com/ws');
  });
  it('turns a pasted browser address into its WebSocket form', () => {
    expect(normalizeGatewayUrl('https://chat.example.com/')).toBe('wss://chat.example.com');
    expect(normalizeGatewayUrl('http://192.168.1.5:7474')).toBe('ws://192.168.1.5:7474');
    expect(normalizeGatewayUrl('192.168.1.5:7474')).toBe('ws://192.168.1.5:7474');
    expect(normalizeGatewayUrl('ws://[::1]:7474')).toBe('ws://[::1]:7474');
    expect(normalizeGatewayUrl('ws://localhost:7474')).toBe('ws://localhost:7474');
  });
  it('refuses anything that is not a gateway address', () => {
    for (const bad of ['', '   ', 'not a url', 'ftp://x.example', 'javascript:alert(1)', 'ws://user:pw@host', 'ws://not%20a%20url', 'ws://a..b']) {
      expect(normalizeGatewayUrl(bad)).toBeNull();
    }
  });
});

describe('planThreadCatchUp (connect without a history fetch per room)', () => {
  const rooms = Array.from({ length: 70 }, (_, i) => ({ id: `r${i}`, unread_count: i % 3 }));
  it('sets badges from the list and fetches history only for the open thread', () => {
    const plan = planThreadCatchUp(rooms, { activeId: 'r5' });
    expect(plan.fetch).toEqual(['r5']);
    expect(Object.keys(plan.counts)).toHaveLength(69);
    expect(plan.counts.r4).toBe(1);
    expect(plan.counts.r5).toBeUndefined();
  });
  it('sends nothing per room when no thread is open', () => {
    expect(planThreadCatchUp(rooms).fetch).toEqual([]);
  });
  it('refetches the open thread once per connection though the list arrives twice', () => {
    const fetched = new Set();
    expect(planThreadCatchUp(rooms, { activeId: 'r1', fetched }).fetch).toEqual(['r1']);
    expect(planThreadCatchUp(rooms, { activeId: 'r1', fetched }).fetch).toEqual([]);
  });
  it('sets rather than adds, so the counts do not grow per delivery', () => {
    const a = planThreadCatchUp(rooms).counts;
    const b = planThreadCatchUp(rooms).counts;
    expect(b).toEqual(a);
  });
  it('skips muted threads and entries without a count', () => {
    const plan = planThreadCatchUp([{ id: 'm', unread_count: 4 }, { id: 'old' }], { muted: new Set(['m']) });
    expect(plan.counts).toEqual({});
  });
});

describe('createRateRetry', () => {
  function make(max) {
    const sent = [], timers = [];
    const rr = createRateRetry({
      send: (p) => sent.push(p),
      payloadFor: (cmd) => (cmd === 'get_local_history' ? null : { cmd }),
      schedule: (fn, ms) => timers.push({ fn, ms }),
      maxAttempts: max,
    });
    return { rr, sent, timers };
  }
  it('resends a refused get_rooms after retry_after', () => {
    const { rr, sent, timers } = make(3);
    expect(rr.handle({ type: 'error', code: 'E_RATE', message: 'rate_limited', cmd: 'get_rooms', retry_after: 4 })).toBe(true);
    expect(timers[0].ms).toBe(4000);
    timers[0].fn();
    expect(sent).toEqual([{ cmd: 'get_rooms' }]);
  });
  it('does not retry writes or other errors', () => {
    const { rr } = make(3);
    expect(rr.handle({ code: 'E_RATE', cmd: 'send_room' })).toBe(false);
    expect(rr.handle({ code: 'E_SCHEMA', cmd: 'get_rooms' })).toBe(false);
    expect(rr.handle({ code: 'E_RATE' })).toBe(false);
  });
  it('gives up after maxAttempts so the error is shown, until reset', () => {
    const { rr } = make(2);
    const ev = { code: 'E_RATE', cmd: 'get_dms', retry_after: 1 };
    expect(rr.handle(ev)).toBe(true);
    expect(rr.handle(ev)).toBe(true);
    expect(rr.handle(ev)).toBe(false);
    rr.reset();
    expect(rr.handle(ev)).toBe(true);
  });
  it('drops a retry whose payload no longer applies', () => {
    const { rr, sent, timers } = make(3);
    rr.handle({ code: 'E_RATE', cmd: 'get_local_history', retry_after: 1 });
    timers[0].fn();
    expect(sent).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Send and connection resilience (single-flight connect, heartbeat, pacing,
// durable offline queue).

async function openCurrent() {
  const ws = hostSocket;
  ws.readyState = FakeWS.OPEN;
  await ws.onopen();
  return ws;
}
function dropCurrent() { hostSocket.readyState = FakeWS.CLOSED; hostSocket.onclose(); }
function liveSockets() { return made.filter(s => s.readyState !== FakeWS.CLOSED); }

describe('single-flight connect', () => {
  it('a send during the reconnect backoff opens one socket, not two', () => {
    const c = make();
    c.connect();
    dropCurrent();                 // instant retry
    vi.advanceTimersByTime(0);
    dropCurrent();                 // second failure: 6s backoff scheduled
    expect(c.state._retryTimeout).not.toBe(null);
    const before = made.length;
    c.socketSendOrQueue({ cmd: 'send_room', room_id: 'r', content: 'x' });
    expect(made.length).toBe(before + 1);
    vi.advanceTimersByTime(7000);  // the backoff connect must not open another
    expect(made.length).toBe(before + 1);
    expect(liveSockets()).toHaveLength(1);
  });
  it('connect() is a no-op while a socket is connecting or open', async () => {
    const c = make();
    c.connect();
    c.connect();
    expect(made).toHaveLength(1);
    await openCurrent();
    c.connect();
    expect(made).toHaveLength(1);
  });
});

describe('heartbeat', () => {
  it('pings an open socket and reconnects when nothing comes back', async () => {
    const c = make();
    c.connect();
    const ws = await openCurrent();
    vi.advanceTimersByTime(25000);
    expect(ws.sent.some(m => m.cmd === 'ping')).toBe(true);
    expect(made).toHaveLength(1);
    vi.advanceTimersByTime(10000);   // no pong: the socket is half-open
    expect(made).toHaveLength(2);
    expect(hostSocket).toBe(made[1]);
    expect(ws.readyState).toBe(FakeWS.CLOSED);
  });
  it('a pong keeps the socket and is not dispatched', async () => {
    const handleEventAsync = vi.fn();
    const c = make({ handleEventAsync });
    c.connect();
    const ws = await openCurrent();
    vi.advanceTimersByTime(25000);
    ws.onmessage({ data: JSON.stringify({ type: 'pong' }) });
    vi.advanceTimersByTime(10000);
    expect(made).toHaveLength(1);
    expect(handleEventAsync).not.toHaveBeenCalled();
  });
  it('probe() on a dead open socket reconnects within its timeout', async () => {
    const c = make();
    c.connect();
    const ws = await openCurrent();
    c.probe(3000);
    expect(ws.sent.at(-1)).toEqual({ cmd: 'ping' });
    vi.advanceTimersByTime(3000);
    expect(made).toHaveLength(2);
  });
  it('going online or back to the tab probes the connection', async () => {
    const winH = {}, docH = {};
    global.window = { addEventListener: (t, f) => { winH[t] = f; } };
    global.document.addEventListener = (t, f) => { docH[t] = f; };
    global.document.visibilityState = 'visible';
    try {
      const c = make();
      c.connect();
      const ws = await openCurrent();
      winH.online();
      expect(ws.sent.at(-1)).toEqual({ cmd: 'ping' });
      ws.onmessage({ data: JSON.stringify({ type: 'pong' }) });
      docH.visibilitychange();
      expect(ws.sent.filter(m => m.cmd === 'ping')).toHaveLength(2);
      // A closed socket reconnects at once instead of waiting out the backoff.
      dropCurrent(); vi.advanceTimersByTime(0); dropCurrent();
      const n = made.length;
      winH.online();
      expect(made.length).toBe(n + 1);
    } finally { delete global.window; }
  });
  it('tolerates a frame that is not JSON', async () => {
    const handleEventAsync = vi.fn();
    const c = make({ handleEventAsync });
    c.connect();
    const ws = await openCurrent();
    expect(() => ws.onmessage({ data: 'not json' })).not.toThrow();
    expect(handleEventAsync).not.toHaveBeenCalled();
  });
});

describe('send pacing', () => {
  it('holds writes past the gateway budget and sends them when the window allows', async () => {
    const c = make();
    c.connect();
    const ws = await openCurrent();
    for (let i = 0; i < 70; i++) c.socketSendOrQueue({ cmd: 'send_room', room_id: 'r', content: String(i) });
    expect(ws.sent.filter(m => m.cmd === 'send_room').length).toBe(PACE_LIMIT - 1);  // register used one slot
    // Snapshot reads and pings have their own gateway budget: never held.
    ws.send(JSON.stringify({ cmd: 'get_rooms' }));
    expect(ws.sent.at(-1)).toEqual({ cmd: 'get_rooms' });
    vi.advanceTimersByTime(10000);
    const sends = ws.sent.filter(m => m.cmd === 'send_room');
    expect(sends.map(m => m.content)).toEqual(Array.from({ length: 70 }, (_, i) => String(i)));
  });
  it('requeues held frames when the socket drops so they go out after register', async () => {
    const c = make();
    c.connect();
    await openCurrent();
    for (let i = 0; i < 60; i++) c.socketSendOrQueue({ cmd: 'send_room', room_id: 'r', content: String(i) });
    const held = 60 - (PACE_LIMIT - 1);
    dropCurrent();
    expect(c.state._pendingOnConnect.map(p => p.payload.content))
      .toEqual(Array.from({ length: held }, (_, i) => String(PACE_LIMIT - 1 + i)));
  });
  it('createSendPacer keeps a sliding window', () => {
    let t = 0; const out = []; const timers = [];
    const p = createSendPacer({ send: (d) => out.push(d), limit: 2, windowMs: 1000, now: () => t,
      schedule: (fn, ms) => { timers.push({ fn, at: t + ms }); return timers.length; }, cancel: () => {} });
    p.push('{"cmd":"a"}'); p.push('{"cmd":"b"}'); p.push('{"cmd":"c"}');
    expect(out).toEqual(['{"cmd":"a"}', '{"cmd":"b"}']);
    expect(timers[0].at).toBe(1000);
    t = 1000; timers[0].fn();
    expect(out).toHaveLength(3);
  });
});

describe('durable offline queue', () => {
  function mkPersist(initial = []) {
    const rows = new Map(initial.map(e => [e.key, e]));
    return {
      rows,
      save: vi.fn(async (e) => { rows.set(e.key, e); }),
      remove: vi.fn(async (k) => { rows.delete(k); }),
      load: vi.fn(async () => [...rows.values()]),
    };
  }
  it('persists queued message writes and forgets them once sent', async () => {
    const persist = mkPersist();
    const c = make({ persist });
    c.connect();
    const send = { cmd: 'send_room', room_id: 'r', content: 'offline', message_id: 'm1' };
    c.socketSendOrQueue(send);
    c.socketSendOrQueue({ cmd: 'typing', room_id: 'r' });       // ephemeral: not persisted
    c.socketSendOrQueue({ cmd: 'edit_local_message', message_id: 'm0', thread_id: 'r', content: 'e' });
    await Promise.resolve();
    expect(persist.rows.size).toBe(2);
    const [row] = persist.rows.values();
    expect(row).toMatchObject({ key: queueKey(send), owner: 'did:key:zSelf', payload: send });
    const ws = await openCurrent();
    c.flushPending();
    await Promise.resolve();
    expect(ws.sent.filter(m => m.cmd === 'send_room')).toHaveLength(1);
    expect(persist.rows.size).toBe(0);
  });
  it('restores a queue left by a closed tab, only for this identity, once', async () => {
    const mine = { cmd: 'send_room', room_id: 'r', content: 'left', message_id: 'm2' };
    const theirs = { cmd: 'send_room', room_id: 'r', content: 'other', message_id: 'm3' };
    const persist = mkPersist([
      { key: queueKey(mine), owner: 'did:key:zSelf', payload: mine, queued_at: 1 },
      { key: queueKey(theirs), owner: 'did:key:zOther', payload: theirs, queued_at: 2 },
    ]);
    const c = make({ persist });
    c.connect();
    const ws = await openCurrent();
    await c.restorePersisted();
    await c.restorePersisted();
    expect(ws.sent.filter(m => m.cmd === 'send_room')).toEqual([mine]);
    expect([...persist.rows.keys()]).toEqual([queueKey(theirs)]);
  });
  it('sendOnce skips a frame this tab already sent (handoff and restore race)', async () => {
    const persist = mkPersist();
    const c = make({ persist });
    c.connect();
    const ws = await openCurrent();
    const p = { cmd: 'send_dm', cert_id: 'c', content: 'x', message_id: 'm4' };
    c.socketSendOrQueue(p);
    c.sendOnce(p);
    expect(ws.sent.filter(m => m.cmd === 'send_dm')).toHaveLength(1);
  });
});

describe('catchUpUnread (badges do not double on reconnect)', () => {
  it('the same catch-up arriving twice leaves the badge unchanged', () => {
    let badge = 0;
    badge = catchUpUnread(badge, 3);
    badge = catchUpUnread(badge, 3);
    expect(badge).toBe(3);
  });
  it('keeps a live count that is already higher', () => {
    expect(catchUpUnread(5, 3)).toBe(5);
    expect(catchUpUnread(undefined, 2)).toBe(2);
  });
});
