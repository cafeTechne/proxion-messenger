import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createPush } from './push.js';

let sent, socketOpen, perm, vapidOk, existingSub, subscribedWith;
beforeEach(() => {
  sent = [];
  socketOpen = true;
  perm = 'granted';
  vapidOk = true;
  existingSub = null;
  subscribedWith = null;
  global.WebSocket = { OPEN: 1 };
  global.atob = (b64) => Buffer.from(b64, 'base64').toString('binary');
  global.Notification = { get permission() { return perm; }, requestPermission: vi.fn(async () => 'granted') };
  const fakeSub = {
    endpoint: 'https://push.example/abc',
    toJSON: () => ({ keys: { p256dh: 'P256DH', auth: 'AUTH' } }),
  };
  const pushManager = {
    getSubscription: vi.fn(async () => existingSub),
    subscribe: vi.fn(async (opts) => { subscribedWith = opts; return fakeSub; }),
  };
  global.navigator = { serviceWorker: { ready: Promise.resolve({ pushManager }) } };
  global.window = { PushManager: function () {} };
  global.fetch = vi.fn(async () => ({
    ok: vapidOk,
    json: async () => ({ publicKey: 'BHl0bWFpbg' }),  // base64url-ish
  }));
});

function make() {
  return createPush({ getSocket: () => (socketOpen ? { readyState: 1, send: (m) => sent.push(JSON.parse(m)) } : { readyState: 3 }) });
}

describe('_urlB64ToUint8Array', () => {
  it('decodes base64url (with - and _) to bytes', () => {
    const out = make()._urlB64ToUint8Array('aGVsbG8');  // "hello"
    expect(Array.from(out)).toEqual([104, 101, 108, 108, 111]);
  });
});

describe('enablePush', () => {
  it('subscribes and registers the subscription with the gateway', async () => {
    const ok = await make().enablePush();
    expect(ok).toBe(true);
    expect(subscribedWith.userVisibleOnly).toBe(true);
    expect(subscribedWith.applicationServerKey).toBeInstanceOf(Uint8Array);
    expect(sent).toEqual([{
      cmd: 'subscribe_push',
      endpoint: 'https://push.example/abc',
      p256dh_b64: 'P256DH',
      auth_b64: 'AUTH',
    }]);
  });
  it('reuses an existing subscription instead of re-subscribing', async () => {
    existingSub = { endpoint: 'https://push.example/old', toJSON: () => ({ keys: { p256dh: 'OLD', auth: 'A2' } }) };
    const p = make();
    const ok = await p.enablePush();
    expect(ok).toBe(true);
    expect(sent[0].endpoint).toBe('https://push.example/old');
  });
  it('no-ops when notification permission is denied', async () => {
    perm = 'denied';
    expect(await make().enablePush()).toBe(false);
    expect(sent).toHaveLength(0);
  });
  it('requests permission when undecided, then subscribes on grant', async () => {
    perm = 'default';
    const ok = await make().enablePush();
    expect(global.Notification.requestPermission).toHaveBeenCalled();
    expect(ok).toBe(true);
  });
  it('returns false when the VAPID key endpoint is unavailable', async () => {
    vapidOk = false;
    expect(await make().enablePush()).toBe(false);
    expect(sent).toHaveLength(0);
  });
});
