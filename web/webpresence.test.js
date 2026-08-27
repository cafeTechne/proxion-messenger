import { describe, it, expect, vi } from 'vitest';
import { statusFromHeartbeat, createWebPresence } from './webpresence.js';

describe('statusFromHeartbeat', () => {
    const now = 1_000_000_000;
    it('is offline with no doc or no heartbeat', () => {
        expect(statusFromHeartbeat(null, now).status).toBe('offline');
        expect(statusFromHeartbeat({ status: 'online' }, now).status).toBe('offline');
    });
    it('keeps the doc status for a fresh heartbeat', () => {
        expect(statusFromHeartbeat({ status: 'online', heartbeat: now - 1000 }, now).status).toBe('online');
        expect(statusFromHeartbeat({ status: 'busy', heartbeat: now - 1000 }, now).status).toBe('busy');
    });
    it('decays to away then offline as the heartbeat ages', () => {
        expect(statusFromHeartbeat({ status: 'online', heartbeat: now - 60_000 }, now).status).toBe('away');
        expect(statusFromHeartbeat({ status: 'online', heartbeat: now - 999_000 }, now).status).toBe('offline');
    });
    it('honors an explicit offline doc even if fresh', () => {
        expect(statusFromHeartbeat({ status: 'offline', heartbeat: now - 100 }, now).status).toBe('offline');
    });
    it('reports lastSeen as the heartbeat time', () => {
        expect(statusFromHeartbeat({ status: 'online', heartbeat: 42 }, now).lastSeen).toBe(42);
    });
    it('prefers the server time (serverMs) over a skewed heartbeat for freshness', () => {
        // Writer clock is 10 min ahead: heartbeat looks fresh, but the server wrote
        // it 10 min ago, so it should read offline. lastSeen stays the heartbeat.
        const doc = { status: 'online', heartbeat: now + 600_000, serverMs: now - 600_000 };
        const r = statusFromHeartbeat(doc, now);
        expect(r.status).toBe('offline');
        expect(r.lastSeen).toBe(now + 600_000);
    });
    it('falls back to the heartbeat when there is no server time', () => {
        expect(statusFromHeartbeat({ status: 'online', heartbeat: now - 1000, serverMs: null }, now).status).toBe('online');
    });
});

function harness({ presence = {}, contacts = [] } = {}) {
    const events = [];
    const written = [];
    const pod = {
        podWritePresence: vi.fn(async (s) => { written.push(s); return true; }),
        podReadPresence: vi.fn(async (root) => presence[root] || null),
        presenceUrlFor: (root) => root + 'proxion/presence.json',
    };
    const notify = { watchResource: vi.fn(() => () => {}) };
    const eng = createWebPresence({
        pod, notify, handleEvent: (e) => events.push(e),
        getContacts: () => contacts,
        peerPodRoot: (webid) => webid.replace(/profile\/card#me$/, ''),
        now: () => 1000,
        heartbeatMs: 999999,
    });
    return { eng, pod, notify, events, written };
}

describe('createWebPresence', () => {
    it('subscribing a contact reads presence and emits presence_update', async () => {
        const { eng, notify, events } = harness({
            presence: { 'https://bob.example/': { status: 'online', heartbeat: 1000 } },
        });
        eng.subscribeContact('https://bob.example/profile/card#me');
        await Promise.resolve(); await Promise.resolve();
        expect(notify.watchResource).toHaveBeenCalled();
        const ev = events.find((e) => e.type === 'presence_update');
        expect(ev).toMatchObject({ webid: 'https://bob.example/profile/card#me', status: 'online' });
    });

    it('a stale heartbeat is reported away/offline', async () => {
        const { eng, events } = harness({
            presence: { 'https://old.example/': { status: 'online', heartbeat: 1000 - 60_000 } },
        });
        eng.subscribeContact('https://old.example/profile/card#me');
        await Promise.resolve(); await Promise.resolve();
        expect(events.find((e) => e.type === 'presence_update').status).toBe('away');
    });

    it('does not subscribe the same contact twice', () => {
        const { eng, notify } = harness({ presence: { 'https://x/': { status: 'online', heartbeat: 1000 } } });
        eng.subscribeContact('https://x/profile/card#me');
        eng.subscribeContact('https://x/profile/card#me');
        expect(notify.watchResource).toHaveBeenCalledTimes(1);
        expect(eng._subs.size).toBe(1);
    });

    it('start() beats online and syncs contacts', async () => {
        const { eng, written, pod } = harness({
            contacts: ['https://a/profile/card#me', 'https://b/profile/card#me'],
            presence: {},
        });
        eng.start();
        expect(written[0]).toBe('online');
        expect(pod.podReadPresence).toHaveBeenCalledTimes(2);
        eng.stop();
    });

    it('start() is idempotent: a second call does not re-beat or start a second timer', () => {
        const { eng, written } = harness();
        const setSpy = vi.spyOn(globalThis, 'setInterval');
        eng.start();
        eng.start();   // e.g. a reconnect calling start again
        expect(written).toEqual(['online']);       // only the first start's beat
        expect(setSpy).toHaveBeenCalledTimes(1);   // one heartbeat timer, not two
        eng.stop();
        setSpy.mockRestore();
    });

    it('stop() re-enables a subsequent start (not permanently disabled)', () => {
        const { eng, written } = harness();
        eng.start();
        eng.stop();
        eng.start();
        expect(written).toEqual(['online', 'online']);   // a real beat on each start
        eng.stop();
    });

    it('caps the number of live subscriptions', () => {
        const many = Array.from({ length: 5 }, (_, i) => `https://p${i}/profile/card#me`);
        const { eng } = harness({ contacts: many });
        const capped = createWebPresence({
            pod: { podWritePresence: vi.fn(), podReadPresence: vi.fn(async () => null), presenceUrlFor: (r) => r },
            notify: { watchResource: () => () => {} }, handleEvent: () => {},
            getContacts: () => many, peerPodRoot: (w) => w, maxSubs: 2,
        });
        capped.syncContacts();
        expect(capped._subs.size).toBe(2);
        eng.stop(); capped.stop();
    });
});
