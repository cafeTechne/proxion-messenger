// notify.test.js — PLAN_ROUND_70 Track A. Two parts: watchResource's
// upgrade/fallback state machine (injecting a fake `connect`), and the direct
// v0.3 discovery + subscribe against a mocked session.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const h = vi.hoisted(() => ({ fetch: vi.fn() }));
vi.mock('./auth.js', () => ({
    solidSession: { fetch: h.fetch, info: { isLoggedIn: true } },
}));

import { watchResource, discoverWebSocketService, subscribeWebSocket, subscribeWebhook,
         discoverStreamingService, subscribeStreamingHttp } from './notify.js';

const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

// A controllable fake connector: captures the handlers and returns per `ctl`.
function fakeConnect(ctl) {
    return (url, handlers) => {
        ctl.handlers = handlers;
        if (ctl.reject) return Promise.reject(new Error('connect failed'));
        if (ctl.returnNull) return Promise.resolve(null);
        ctl.close = vi.fn();
        return Promise.resolve(ctl.close);
    };
}

describe('watchResource state machine', () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());

    it('polls when the server advertises no notification service', async () => {
        const onChange = vi.fn();
        watchResource('https://p/d', onChange, { pollMs: 1000, connect: fakeConnect({ returnNull: true }) });
        await flush();
        vi.advanceTimersByTime(1000);
        expect(onChange).toHaveBeenCalledTimes(1);
        vi.advanceTimersByTime(1000);
        expect(onChange).toHaveBeenCalledTimes(2);
    });

    it('upgrades: stops polling on open, fires on message', async () => {
        const onChange = vi.fn();
        const ctl = {};
        watchResource('https://p/d', onChange, { pollMs: 1000, connect: fakeConnect(ctl) });
        await flush();
        ctl.handlers.onOpen();
        vi.advanceTimersByTime(5000);
        expect(onChange).not.toHaveBeenCalled();     // no polling while live
        ctl.handlers.onMessage();
        expect(onChange).toHaveBeenCalledTimes(1);
    });

    it('resumes polling if the socket closes', async () => {
        const onChange = vi.fn();
        const ctl = {};
        watchResource('https://p/d', onChange, { pollMs: 1000, connect: fakeConnect(ctl) });
        await flush();
        ctl.handlers.onOpen();
        ctl.handlers.onClose();
        vi.advanceTimersByTime(1000);
        expect(onChange).toHaveBeenCalledTimes(1);
    });

    it('keeps polling if connect rejects', async () => {
        const onChange = vi.fn();
        watchResource('https://p/d', onChange, { pollMs: 1000, connect: fakeConnect({ reject: true }) });
        await flush();
        vi.advanceTimersByTime(1000);
        expect(onChange).toHaveBeenCalledTimes(1);
    });

    it('unsubscribe closes the socket and stops polling', async () => {
        const onChange = vi.fn();
        const ctl = {};
        const unsub = watchResource('https://p/d', onChange, { pollMs: 1000, connect: fakeConnect(ctl) });
        await flush();
        unsub();
        expect(ctl.close).toHaveBeenCalled();
        vi.advanceTimersByTime(10000);
        expect(onChange).not.toHaveBeenCalled();
    });

    it('a message after unsubscribe does not fire onChange', async () => {
        const onChange = vi.fn();
        const ctl = {};
        const unsub = watchResource('https://p/d', onChange, { pollMs: 1000, connect: fakeConnect(ctl) });
        await flush();
        unsub();
        ctl.handlers.onMessage();
        expect(onChange).not.toHaveBeenCalled();
    });
});

describe('direct v0.3 discovery + subscribe', () => {
    const LINK = '<https://p/.well-known/solid>; rel="http://www.w3.org/ns/solid/terms#storageDescription"';
    const WS = 'http://www.w3.org/ns/solid/notifications#WebSocketChannel2023';
    const CT = 'http://www.w3.org/ns/solid/notifications#channelType';
    const DESC = { '@graph': [{ '@id': 'https://p/.notifications/WebSocketChannel2023/', [CT]: [{ '@id': WS }] }] };

    beforeEach(() => h.fetch.mockReset());

    it('discovers the WebSocketChannel2023 service from the storage description', async () => {
        h.fetch.mockImplementation(async (url, opts) => {
            if ((opts?.method) === 'HEAD') return { headers: { get: () => LINK } };
            return { ok: true, json: async () => DESC };
        });
        expect(await discoverWebSocketService('https://p/d/chat.ttl'))
            .toBe('https://p/.notifications/WebSocketChannel2023/');
    });

    it('returns null when the resource advertises no storage description', async () => {
        h.fetch.mockImplementation(async () => ({ headers: { get: () => null } }));
        expect(await discoverWebSocketService('https://p/d/chat.ttl')).toBe(null);
    });

    it('subscribes and returns the receiveFrom socket URL', async () => {
        h.fetch.mockImplementation(async (url, opts) => {
            if ((opts?.method) === 'HEAD') return { headers: { get: () => LINK } };
            if ((opts?.method) === 'POST') {
                // The subscription must carry the fully-qualified channel-type IRI.
                expect(String(opts.body)).toContain(WS);
                return { ok: true, json: async () => ({ receiveFrom: 'ws://p/rx/abc' }) };
            }
            return { ok: true, json: async () => DESC };
        });
        expect(await subscribeWebSocket('https://p/d/chat.ttl')).toBe('ws://p/rx/abc');
    });

    it('returns null when subscription is refused', async () => {
        h.fetch.mockImplementation(async (url, opts) => {
            if ((opts?.method) === 'HEAD') return { headers: { get: () => LINK } };
            if ((opts?.method) === 'POST') return { ok: false, status: 422, json: async () => ({}) };
            return { ok: true, json: async () => DESC };
        });
        expect(await subscribeWebSocket('https://p/d/chat.ttl')).toBe(null);
    });
});

describe('StreamingHTTPChannel2023 (R101.4)', () => {
    const LINK = '<https://p/.well-known/solid>; rel="http://www.w3.org/ns/solid/terms#storageDescription"';
    const STREAM = 'http://www.w3.org/ns/solid/notifications#StreamingHTTPChannel2023';
    const CT = 'http://www.w3.org/ns/solid/notifications#channelType';
    const DESC = { '@graph': [{ '@id': 'https://p/.notifications/StreamingHTTPChannel2023/', [CT]: [{ '@id': STREAM }] }] };

    beforeEach(() => h.fetch.mockReset());

    it('discovers the streaming service and subscribes with the right channel type', async () => {
        h.fetch.mockImplementation(async (url, opts) => {
            if ((opts?.method) === 'HEAD') return { headers: { get: () => LINK } };
            if ((opts?.method) === 'POST') {
                expect(String(opts.body)).toContain(STREAM);
                return { ok: true, json: async () => ({ receiveFrom: 'https://p/rx/stream' }) };
            }
            return { ok: true, json: async () => DESC };
        });
        expect(await discoverStreamingService('https://p/d/chat.ttl'))
            .toBe('https://p/.notifications/StreamingHTTPChannel2023/');
        expect(await subscribeStreamingHttp('https://p/d/chat.ttl')).toBe('https://p/rx/stream');
    });

    it('returns null when the server offers no streaming channel', async () => {
        h.fetch.mockImplementation(async (url, opts) => {
            if ((opts?.method) === 'HEAD') return { headers: { get: () => LINK } };
            return { ok: true, json: async () => ({ '@graph': [] }) };
        });
        expect(await subscribeStreamingHttp('https://p/d/chat.ttl')).toBe(null);
    });
});

describe('webhook channel (R77 — closed-app inbox push)', () => {
    const LINK = '<https://p/.well-known/solid>; rel="http://www.w3.org/ns/solid/terms#storageDescription"';
    const WH = 'http://www.w3.org/ns/solid/notifications#WebhookChannel2023';
    const CT = 'http://www.w3.org/ns/solid/notifications#channelType';
    const DESC = { '@graph': [{ '@id': 'https://p/.notifications/WebhookChannel2023/', [CT]: [{ '@id': WH }] }] };

    beforeEach(() => h.fetch.mockReset());

    it('subscribes the resource with topic + sendTo, carrying the webhook IRI', async () => {
        let posted = null;
        h.fetch.mockImplementation(async (url, opts) => {
            if ((opts?.method) === 'HEAD') return { headers: { get: () => LINK } };
            if ((opts?.method) === 'POST') { posted = JSON.parse(opts.body); return { ok: true, json: async () => ({}) }; }
            return { ok: true, json: async () => DESC };
        });
        expect(await subscribeWebhook('https://p/inbox/', 'https://gw/solid-webhook/tok')).toBe(true);
        expect(posted.type).toBe(WH);
        expect(posted.topic).toBe('https://p/inbox/');
        expect(posted.sendTo).toBe('https://gw/solid-webhook/tok');
    });

    it('returns false when the server offers no webhook channel', async () => {
        const WS = 'http://www.w3.org/ns/solid/notifications#WebSocketChannel2023';
        const WSONLY = { '@graph': [{ '@id': 'https://p/.notifications/WebSocketChannel2023/', [CT]: [{ '@id': WS }] }] };
        h.fetch.mockImplementation(async (url, opts) => {
            if ((opts?.method) === 'HEAD') return { headers: { get: () => LINK } };
            return { ok: true, json: async () => WSONLY };
        });
        expect(await subscribeWebhook('https://p/inbox/', 'https://gw/solid-webhook/tok')).toBe(false);
    });

    it('returns false without a sendTo target', async () => {
        expect(await subscribeWebhook('https://p/inbox/', '')).toBe(false);
    });
});
