import { afterEach, describe, expect, it, vi } from 'vitest';
import { createPush } from './push.js';

afterEach(() => vi.unstubAllGlobals());
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
function setup(existing = null) {
    const sent = [], socket = { readyState: 1, send: s => sent.push(JSON.parse(s)) };
    let active = socket;
    const fresh = { endpoint: 'https://push.example/new', unsubscribe: vi.fn(async () => true), toJSON: () => ({ keys: { p256dh: 'p', auth: 'a' } }) };
    const manager = { getSubscription: vi.fn(async () => existing), subscribe: vi.fn(async () => fresh) };
    vi.stubGlobal('navigator', { serviceWorker: { ready: Promise.resolve({ pushManager: manager }) } });
    vi.stubGlobal('window', { PushManager: class {} });
    vi.stubGlobal('WebSocket', { OPEN: 1 });
    vi.stubGlobal('Notification', { permission: 'granted' });
    vi.stubGlobal('localStorage', { getItem: () => null, removeItem: vi.fn() });
    vi.stubGlobal('location', { origin: 'https://gateway.example' });
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ publicKey: 'AQID' }) })));
    const push = createPush({ getSocket: () => active });
    return { push, sent, manager, fresh, switchSocket: () => { active = { ...socket }; } };
}
function oldSub(extra = {}) {
    return { endpoint: 'https://push.example/old', options: { applicationServerKey: new Uint8Array([1, 2, 3]).buffer },
        expirationTime: null, unsubscribe: vi.fn(async () => true), toJSON: () => ({ keys: { p256dh: 'p', auth: 'a' } }), ...extra };
}

describe('push subscription recovery', () => {
    it('reuses a live subscription bound to the current gateway key', async () => {
        const h = setup(oldSub()); expect(await h.push.enablePush()).toBe(true);
        expect(h.manager.subscribe).not.toHaveBeenCalled(); expect(h.sent[0].endpoint).toBe('https://push.example/old');
    });
    it.each(['expired', 'rotated'])('renews a subscription whose binding is %s', async reason => {
        const old = oldSub(reason === 'expired' ? { expirationTime: Date.now() - 1 } : { options: { applicationServerKey: new Uint8Array([9]).buffer } });
        const h = setup(old); expect(await h.push.enablePush()).toBe(true);
        expect(old.unsubscribe).toHaveBeenCalledOnce(); expect(h.manager.subscribe).toHaveBeenCalledOnce();
        expect(h.sent[0].endpoint).toBe(h.fresh.endpoint);
    });
    it('does not create a replacement if the old subscription cannot be removed', async () => {
        const h = setup(oldSub({ expirationTime: 1, unsubscribe: vi.fn(async () => false) }));
        expect(await h.push.enablePush()).toBe(false); expect(h.manager.subscribe).not.toHaveBeenCalled(); expect(h.sent).toEqual([]);
    });
    it('coalesces concurrent setup requests', async () => {
        const h = setup(); expect(await Promise.all([h.push.enablePush(), h.push.enablePush()])).toEqual([true, true]);
        expect(h.manager.subscribe).toHaveBeenCalledOnce(); expect(h.sent).toHaveLength(1);
    });
    it.each(['permission', 'socket', 'disabled'])('discards a late subscription after %s changes', async reason => {
        const h = setup(), late = deferred(); h.manager.subscribe.mockReturnValue(late.promise);
        const enabling = h.push.enablePush(); await vi.waitFor(() => expect(h.manager.subscribe).toHaveBeenCalledOnce());
        if (reason === 'permission') Notification.permission = 'denied';
        if (reason === 'socket') h.switchSocket();
        if (reason === 'disabled') await h.push.disablePush();
        late.resolve(h.fresh); expect(await enabling).toBe(false);
        expect(h.sent).toEqual([]); expect(h.fresh.unsubscribe).toHaveBeenCalledOnce();
    });
    it('allows an explicit re-enable after interrupted setup finishes', async () => {
        const h = setup(), late = deferred(); h.manager.subscribe.mockReturnValueOnce(late.promise);
        const old = h.push.enablePush(); await vi.waitFor(() => expect(h.manager.subscribe).toHaveBeenCalledOnce());
        await h.push.disablePush(); const next = h.push.enablePush(); late.resolve(h.fresh);
        expect(await old).toBe(false); expect(await next).toBe(true); expect(h.sent).toHaveLength(1);
    });
});
