import { describe, expect, it, vi } from 'vitest';
import { createRoomAccessMonitor } from './room-access.js';

const ROOT = 'https://joiner.example/';
const OWNER = 'https://owner.example/profile/card#me';
const ROOM = { roomId: 'r1', owner: OWNER, ownerPodRoot: 'https://owner.example/',
    container: 'https://owner.example/proxion/rooms/r1/' };
const URL = ROOM.container + 'members.jsonld';
const PRIVATE = ROOT + 'proxion/joined-rooms.json';
const response = (status, error = '') => ({ status, ok: status >= 200 && status < 300, error });

function harness({ peer = response(200), own = response(200), onRemoved = vi.fn(async () => true), budget, requestTimeoutMs } = {}) {
    let state = { ready: true, webId: ROOT + 'profile/card#me', root: ROOT }, tick;
    const fetch = vi.fn(async (url) => {
        const value = url.startsWith(ROOT) ? own : peer;
        if (value instanceof Error) throw value;
        return typeof value === 'function' ? value(url) : value;
    });
    const clearTimer = vi.fn();
    const monitor = createRoomAccessMonitor({
        fetch, getSession: () => state, onRemoved, maxRoomsPerPoll: budget, requestTimeoutMs,
        setTimer: (fn) => { tick = fn; return 42; }, clearTimer,
    });
    return { monitor, fetch, clearTimer, onRemoved,
        setPeer: (value) => { peer = value; }, setOwn: (value) => { own = value; },
        setSession: (value) => { state = value; }, tick: () => tick(), state: () => state,
    };
}

describe('idle joined-room access', () => {
    it('detects a stable revocation without appending a message', async () => {
        const h = harness({ peer: response(403) });
        h.monitor.track(ROOM);
        expect(await h.monitor.check('r1')).toBe('denied');
        expect(h.onRemoved).not.toHaveBeenCalled();
        expect(await h.monitor.check('r1')).toBe('removed');
        expect(h.onRemoved).toHaveBeenCalledOnce();
        expect(h.onRemoved).toHaveBeenCalledWith('r1', ROOM);
        expect(h.fetch.mock.calls.map(([url]) => url)).toEqual([URL, PRIVATE, URL, PRIVATE, URL]);
        expect(h.fetch.mock.calls.every(([, opts]) => opts.method === 'HEAD' && opts.redirect === 'error' && opts.cache === 'no-store')).toBe(true);
        expect(h.monitor.tracked()).toEqual([]);
        expect(h.clearTimer).toHaveBeenCalledWith(42);
    });

    it.each([401, 404, 429, 500, 503])('keeps rooms after a peer response of %s', async (status) => {
        const h = harness({ peer: response(status) });
        h.monitor.track(ROOM);
        await h.monitor.check('r1');
        await h.monitor.check('r1');
        expect(h.onRemoved).not.toHaveBeenCalled();
        expect(h.monitor.tracked()).toEqual(['r1']);
        expect(h.fetch).toHaveBeenCalledTimes(2);
    });

    it.each([401, 403, 404, 500, 503])('keeps a denied room when the own private session probe returns %s', async (status) => {
        const h = harness({ peer: response(403), own: response(status) });
        h.monitor.track(ROOM);
        await h.monitor.check('r1');
        await h.monitor.check('r1', { denied: true });
        expect(h.onRemoved).not.toHaveBeenCalled();
        expect(h.monitor.tracked()).toEqual(['r1']);
    });

    it('uses the private legacy record only when the canonical probe is missing', async () => {
        const h = harness({ peer: response(403), own: (url) => response(url.endsWith('.jsonld') ? 200 : 404) });
        h.monitor.track(ROOM);
        expect(await h.monitor.check('r1', { denied: true })).toBe('removed');
        expect(h.fetch.mock.calls.some(([url]) => url === ROOT + 'proxion/joined-rooms.jsonld')).toBe(true);
    });

    it('keeps rooms after network failures and bridge-policy refusals', async () => {
        const h = harness({ peer: new TypeError('offline') });
        h.monitor.track(ROOM);
        expect(await h.monitor.check('r1')).toBe('unavailable');
        h.setPeer(response(403, 'path_not_allowed'));
        expect(await h.monitor.check('r1', { denied: true })).toBe('unavailable');
        h.setPeer(response(403));
        h.setOwn(new TypeError('own pod offline'));
        expect(await h.monitor.check('r1')).toBe('unavailable');
        expect(h.onRemoved).not.toHaveBeenCalled();
    });

    it('requires consecutive denials and cancels a revocation after recovery', async () => {
        const h = harness({ peer: response(403) });
        h.monitor.track(ROOM);
        await h.monitor.check('r1');
        h.setPeer(response(200));
        expect(await h.monitor.check('r1')).toBe('allowed');
        h.setPeer(response(403));
        expect(await h.monitor.check('r1')).toBe('denied');
        let requests = 0;
        h.setPeer(() => response(++requests === 1 ? 403 : 200));
        expect(await h.monitor.check('r1')).toBe('allowed');
        expect(h.onRemoved).not.toHaveBeenCalled();
    });

    it('an observed append denial still verifies readable membership before removing', async () => {
        const h = harness({ peer: response(200) });
        h.monitor.track(ROOM);
        expect(await h.monitor.check('r1', { denied: true })).toBe('allowed');
        expect(h.onRemoved).not.toHaveBeenCalled();
    });

    it('retries durable cleanup failures and reports removal once only', async () => {
        const onRemoved = vi.fn().mockResolvedValueOnce(false).mockRejectedValueOnce(new Error('offline')).mockResolvedValue(true);
        const h = harness({ peer: response(403), onRemoved });
        h.monitor.track(ROOM);
        expect(await h.monitor.check('r1', { denied: true })).toBe('denied');
        expect(h.monitor.tracked()).toEqual(['r1']);
        expect(await h.monitor.check('r1')).toBe('denied');
        expect(await h.monitor.check('r1')).toBe('removed');
        expect(await h.monitor.check('r1')).toBe('stale');
        expect(onRemoved).toHaveBeenCalledTimes(3);
    });

    it('serializes overlapping checks while a probe is pending', async () => {
        let finish;
        const h = harness({ peer: () => new Promise((resolve) => { finish = resolve; }) });
        h.monitor.track(ROOM);
        const first = h.monitor.check('r1');
        const second = h.monitor.check('r1');
        expect(second).toBe(first);
        expect(h.fetch).toHaveBeenCalledOnce();
        finish(response(200));
        await first;
    });

    it('never removes a room after the account changes during an auth probe', async () => {
        let finish;
        const h = harness({ peer: response(403), own: () => new Promise((resolve) => { finish = resolve; }) });
        h.monitor.track(ROOM);
        const checking = h.monitor.check('r1', { denied: true });
        await vi.waitFor(() => expect(finish).toBeTypeOf('function'));
        h.setSession({ ...h.state(), webId: 'https://different.example/profile/card#me' });
        finish(response(200));
        expect(await checking).not.toBe('removed');
        expect(h.onRemoved).not.toHaveBeenCalled();
    });

    it('does not act after logout, untrack or stop while a request is pending', async () => {
        let finish;
        const h = harness({ peer: () => new Promise((resolve) => { finish = resolve; }) });
        h.monitor.track(ROOM);
        const checking = h.monitor.check('r1', { denied: true });
        h.monitor.stop();
        finish(response(403));
        expect(await checking).toBe('stale');
        expect(h.onRemoved).not.toHaveBeenCalled();
        expect(h.monitor.track(ROOM)).toBe(true);
        h.setSession({ ...h.state(), ready: false });
        expect(await h.monitor.check('r1')).toBe('stale');
    });

    it('refuses owned rooms, foreign containers, unsafe roots, and account-owned rooms', () => {
        const h = harness();
        for (const room of [{ ...ROOM, owned: true }, { ...ROOM, owner: h.state().webId },
            { ...ROOM, container: 'https://another.example/rooms/r1/' },
            { ...ROOM, container: 'http://127.0.0.1:1234/private/' },
            { ...ROOM, ownerPodRoot: 'https://another.example/' }]) {
            expect(h.monitor.track(room)).toBe(false);
        }
        expect(h.fetch).not.toHaveBeenCalled();
    });

    it('rotates a bounded sequential batch so many rooms cannot burst bridge requests', async () => {
        const h = harness({ budget: 2 });
        for (let i = 1; i <= 5; i++) h.monitor.track({ ...ROOM, roomId: 'r' + i, container: `https://owner.example/proxion/rooms/r${i}/` });
        await h.monitor.checkAll();
        expect(h.fetch).toHaveBeenCalledTimes(2);
        await h.monitor.checkAll();
        expect(h.fetch.mock.calls[2][0]).toContain('/r3/');
        await h.monitor.checkAll();
        expect(h.fetch.mock.calls[4][0]).toContain('/r5/');
    });
});


describe('access monitor request lifecycle', () => {
    it.each(['stop', 'untrack'])('a pending old request cannot block newly tracked rooms after %s', async (stop) => {
        let finishOld, finishNew;
        const h = harness({ peer: () => new Promise((resolve) => { finishOld = resolve; }) });
        h.monitor.track(ROOM);
        const oldPoll = h.monitor.checkAll();
        if (stop === 'stop') h.monitor.stop(); else h.monitor.untrack('r1');
        h.setPeer(() => new Promise((resolve) => { finishNew = resolve; }));
        h.monitor.track({ ...ROOM, roomId: 'r2', container: 'https://owner.example/proxion/rooms/r2/' });
        const newPoll = h.monitor.checkAll();
        expect(h.fetch).toHaveBeenCalledTimes(2);
        finishOld(response(403));
        await oldPoll;
        // The stale finalizer must not erase the new active poll reference.
        expect(h.monitor.checkAll()).toBe(newPoll);
        finishNew(response(200));
        expect(await newPoll).toEqual(['allowed']);
        expect(h.onRemoved).not.toHaveBeenCalled();
    });

    it('times out a stalled room and continues to check another healthy room', async () => {
        vi.useFakeTimers();
        try {
            let rejectLate;
            const h = harness({ requestTimeoutMs: 10, peer: (url) => url === URL
                ? new Promise((_, reject) => { rejectLate = reject; }) : response(200) });
            h.monitor.track(ROOM);
            h.monitor.track({ ...ROOM, roomId: 'r2', container: 'https://owner.example/proxion/rooms/r2/' });
            const checking = h.monitor.checkAll();
            await vi.advanceTimersByTimeAsync(11);
            expect(await checking).toEqual(['unavailable', 'allowed']);
            expect(h.monitor.tracked()).toEqual(['r1', 'r2']);
            expect(h.onRemoved).not.toHaveBeenCalled();
            // An abort-ignoring fetch/bridge may reject after our deadline.
            // Its rejection handler must remain attached, without an unhandled error.
            rejectLate(new TypeError('late network rejection'));
            await Promise.resolve();
            expect(h.fetch.mock.calls[0][1].signal?.aborted).toBe(true);
        } finally { vi.useRealTimers(); }
    });
});
