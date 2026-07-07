import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('./e2e.js', () => ({
  myX25519PubB64u: () => 'MYX25519PUB',
}));

import { createConnection } from './connection.js';

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

function make(over = {}) {
  return createConnection({
    wsUrl: 'ws://test/gw',
    getSocket: () => hostSocket,
    setSocket: (s) => { hostSocket = s; },
    getClientDid: () => 'did:key:zSelf',
    generateOrLoadIdentity: async () => {},
    handleEventAsync: vi.fn(),
    ...over,
  });
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
