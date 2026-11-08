// gwpod.js: request/response correlation over the gateway socket, timeouts,
// the Response shape, watches, and the podio instance it builds.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { createGwPodBridge, responseFromResult } from './gwpod.js';

const READY = { type: 'pod_bridge', ready: true, webid: 'https://pod.example/a/profile/card#me', pod_root: 'https://pod.example/a/', consumer: true };

afterEach(() => { vi.useRealTimers(); });

describe('responseFromResult', () => {
    it('is fetch-shaped', async () => {
        const r = responseFromResult({ status: 200, headers: { 'Last-Modified': 'x' }, body: '{"a":1}' });
        expect(r.ok).toBe(true);
        expect(r.headers.get('last-modified')).toBe('x');
        expect(await r.json()).toEqual({ a: 1 });
        const d = responseFromResult({ status: 403, error: 'path_not_allowed' });
        expect(d.ok).toBe(false);
        expect(d.error).toBe('path_not_allowed');
        expect(await d.text()).toBe('');
    });
});

describe('createGwPodBridge', () => {
    it('does not time out a request while another request is ahead of it in the serial gateway', async () => {
        vi.useFakeTimers();
        let gateway = Promise.resolve(), bridge;
        bridge = createGwPodBridge({ timeoutMs: 100, send: message => {
            gateway = gateway.then(() => new Promise(resolve => setTimeout(resolve, 80))).then(() => {
                bridge.handleEvent({ type: 'pod_fetch_result', req_id: message.req_id, status: 200, body: message.url });
            });
        } });
        const results = Promise.allSettled([bridge.fetch('https://pod.example/one'), bridge.fetch('https://pod.example/two')]);
        await vi.advanceTimersByTimeAsync(161);
        expect((await results).map(result => result.status)).toEqual(['fulfilled', 'fulfilled']);
    });
    it('correlates results by req_id and dispatches requests in order', async () => {
        const sent = [];
        const b = createGwPodBridge({ send: (m) => sent.push(m) });
        const p1 = b.fetch('https://x.example/1');
        const p2 = b.fetch('https://x.example/2', { method: 'post', body: '{}', headers: { 'Content-Type': 'application/json' } });
        expect(sent).toHaveLength(1);
        b.handleEvent({ type: 'pod_fetch_result', req_id: sent[0].req_id, status: 200, body: 'one' });
        expect(sent[1]).toMatchObject({ cmd: 'pod_fetch', method: 'POST', url: 'https://x.example/2', body: '{}' });
        b.handleEvent({ type: 'pod_fetch_result', req_id: sent[1].req_id, status: 201, body: '' });
        expect((await p2).status).toBe(201);
        expect(await (await p1).text()).toBe('one');
        expect(b._pendingCount()).toBe(0);
    });

    it('rejects like a network error on timeout and on a dropped socket', async () => {
        vi.useFakeTimers();
        const b = createGwPodBridge({ send: () => {}, timeoutMs: 1000 });
        const p = b.fetch('https://x.example/slow');
        vi.advanceTimersByTime(1001);
        await expect(p).rejects.toBeInstanceOf(TypeError);
        const q = b.fetch('https://x.example/lost');
        b.reset();
        await expect(q).rejects.toBeInstanceOf(TypeError);
        expect(b.info().ready).toBe(false);
    });

    it.each(['disconnect', 'account', 'unavailable'])('cancels queued writes without sending them after %s', async reason => {
        const sent = [], bridge = createGwPodBridge({ send: message => sent.push(message) });
        bridge.handleEvent(READY);
        const results = Promise.allSettled([bridge.fetch('https://pod.example/read'),
            bridge.fetch('https://pod.example/write', { method: 'PUT', body: 'old account' })]);
        if (reason === 'disconnect') bridge.reset();
        else bridge.handleEvent({ ...READY, ...(reason === 'account' ? { webid: 'https://other.example/#me', pod_root: 'https://other.example/' } : { ready: false }) });
        expect((await results).every(result => result.status === 'rejected')).toBe(true);
        bridge.handleEvent({ type: 'pod_fetch_result', req_id: sent[0].req_id, status: 200 });
        expect(sent).toHaveLength(1);
        expect(bridge._pendingCount()).toBe(0);
    });

    it('expires queued work without sending it later', async () => {
        vi.useFakeTimers();
        const sent = [], bridge = createGwPodBridge({ timeoutMs: 1000, queueTimeoutMs: 100, send: message => sent.push(message) });
        const results = Promise.allSettled([bridge.fetch('https://pod.example/active'), bridge.fetch('https://pod.example/unsent', { method: 'POST' })]);
        await vi.advanceTimersByTimeAsync(101);
        bridge.handleEvent({ type: 'pod_fetch_result', req_id: sent[0].req_id, status: 200 });
        const settled = await results;
        expect(settled[0].status).toBe('fulfilled');
        expect(settled[1].reason.message).toContain('before dispatch');
        expect(sent).toHaveLength(1);
        expect(bridge._pendingCount()).toBe(0);
    });

    it('bounds the backlog and rejects overload without dispatching the excess request', async () => {
        const sent = [], bridge = createGwPodBridge({ send: message => sent.push(message) });
        const pending = Array.from({ length: 257 }, (_, index) => bridge.fetch('https://pod.example/' + index));
        const results = Promise.allSettled(pending);
        expect(bridge._pendingCount()).toBe(256);
        bridge.reset();
        expect((await results)[256].reason.message).toContain('queue full');
        expect(sent).toHaveLength(1);
    });

    it('releases the queue after timeout and ignores a late acknowledgement', async () => {
        vi.useFakeTimers();
        const sent = [], bridge = createGwPodBridge({ timeoutMs: 100, send: message => sent.push(message) });
        const results = Promise.allSettled([bridge.fetch('https://pod.example/slow'), bridge.fetch('https://pod.example/next')]);
        await vi.advanceTimersByTimeAsync(101);
        expect(sent).toHaveLength(2);
        bridge.handleEvent({ type: 'pod_fetch_result', req_id: sent[0].req_id, status: 200 });
        expect(bridge._pendingCount()).toBe(1);
        bridge.handleEvent({ type: 'pod_fetch_result', req_id: sent[1].req_id, status: 200 });
        expect((await results).map(result => result.status)).toEqual(['rejected', 'fulfilled']);
    });

    it('settles a rejected write once and continues with the next request', async () => {
        const sent = [], bridge = createGwPodBridge({ send: message => sent.push(message) });
        const write = bridge.fetch('https://pod.example/write', { method: 'PUT', body: '{}' });
        const read = bridge.fetch('https://pod.example/read');
        bridge.handleEvent({ type: 'pod_fetch_result', req_id: sent[0].req_id, status: 502, error: 'upstream' });
        expect((await write).status).toBe(502);
        bridge.handleEvent({ type: 'pod_fetch_result', req_id: sent[1].req_id, status: 200 });
        expect((await read).ok).toBe(true);
        expect(sent.map(message => message.method)).toEqual(['PUT', 'GET']);
    });

    it('recovers from a synchronous send failure', async () => {
        const send = vi.fn(() => { throw new Error('socket closed'); });
        const bridge = createGwPodBridge({ send });
        await expect(bridge.fetch('https://pod.example/one')).rejects.toThrow('unavailable');
        send.mockImplementationOnce(message => bridge.handleEvent({ type: 'pod_fetch_result', req_id: message.req_id, status: 200 }));
        expect((await bridge.fetch('https://pod.example/two')).ok).toBe(true);
        expect(bridge._pendingCount()).toBe(0);
    });

    it('ignores unrelated events and unknown req_ids', () => {
        const b = createGwPodBridge({ send: () => {} });
        expect(b.handleEvent({ type: 'message' })).toBe(false);
        expect(b.handleEvent({ type: 'pod_fetch_result', req_id: 'nope', status: 200 })).toBe(true);
    });

    it('watches: one pod_watch per url, unwatch when the last listener leaves, resubscribe re-arms', () => {
        const sent = [];
        const b = createGwPodBridge({ send: (m) => sent.push(m) });
        const cb1 = vi.fn(), cb2 = vi.fn();
        const u1 = b.watchResource('https://p/x/', cb1);
        const u2 = b.watchResource('https://p/x/', cb2);
        expect(sent.filter((m) => m.cmd === 'pod_watch')).toHaveLength(1);
        b.handleEvent({ type: 'pod_changed', url: 'https://p/x/' });
        expect(cb1).toHaveBeenCalled(); expect(cb2).toHaveBeenCalled();
        b.watchResource('https://p/x/', vi.fn(), { fast: true });
        expect(sent.filter((m) => m.cmd === 'pod_watch').pop().fast).toBe(true);
        sent.length = 0;
        b.resubscribe();
        expect(sent).toEqual([{ cmd: 'pod_watch', url: 'https://p/x/', fast: true }]);
        u1(); u2();
        expect(sent.some((m) => m.cmd === 'pod_unwatch')).toBe(false);   // one listener left
    });

    it('setFast speeds a watch up and slows it down, and only for watched urls', () => {
        const sent = [];
        const b = createGwPodBridge({ send: (m) => sent.push(m) });
        b.watchResource('https://p/call/', vi.fn());
        b.setFast('https://p/call/', true);
        b.setFast('https://p/call/', true);
        b.setFast('https://p/call/', false);
        b.setFast('https://p/other/', true);
        expect(sent.filter((m) => m.cmd === 'pod_watch').map((m) => [m.url, m.fast]))
            .toEqual([['https://p/call/', false], ['https://p/call/', true], ['https://p/call/', false]]);
        sent.length = 0;
        b.resubscribe();
        expect(sent).toEqual([{ cmd: 'pod_watch', url: 'https://p/call/', fast: false }]);
    });

    it('the podio instance is inert until the bridge is ready, then uses our pod root', async () => {
        const sent = [];
        const b = createGwPodBridge({ send: (m) => sent.push(m) });
        expect(await b.io.podEnsureDmInbox()).toBe(null);
        expect(sent).toEqual([]);
        b.handleEvent(READY);
        const p = b.io.podDropDm('https://pod.example/b/', { from_webid: READY.webid });
        await Promise.resolve();
        expect(sent[0]).toMatchObject({ cmd: 'pod_fetch', method: 'POST', url: 'https://pod.example/b/proxion/dm-inbox/' });
        b.handleEvent({ type: 'pod_fetch_result', req_id: sent[0].req_id, status: 201 });
        expect(await p).toBe(true);
    });

    it('never puts credentials in a request', () => {
        const sent = [];
        const b = createGwPodBridge({ send: (m) => sent.push(m) });
        b.fetch('https://x.example/1', { headers: { Accept: 'application/json' } });
        expect(Object.keys(sent[0].headers)).toEqual(['Accept']);
    });
});
