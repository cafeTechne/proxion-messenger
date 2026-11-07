import { describe, it, expect, vi } from 'vitest';
import { createPodIo } from './podio.js';
import {
    createRoomSync, normalizeJoinedRecord, normalizeJoinedList, withJoinedRecord, withoutJoinedRecord,
    joinedCacheKey, joinPendingKey, LEGACY_JOINED_ROOMS_UNREADABLE, createJoinedRoomStore,
} from './webrooms.js';

const REC = {
    roomId: 'room-1', owner: 'https://alice.example/profile/card#me',
    ownerPodRoot: 'https://alice.example/', container: 'https://alice.example/proxion/rooms/room-1/', title: 'General',
};

describe('joined-room records', () => {
    it('keeps a well-formed record and only its known fields', () => {
        expect(normalizeJoinedRecord({ ...REC, extra: 'x' })).toEqual(REC);
    });
    it('rejects malformed records', () => {
        expect(normalizeJoinedRecord(null)).toBe(null);
        expect(normalizeJoinedRecord({ ...REC, roomId: '../x' })).toBe(null);
        expect(normalizeJoinedRecord({ ...REC, owner: 'javascript:1' })).toBe(null);
        expect(normalizeJoinedRecord({ ...REC, container: 'https://alice.example/no-slash' })).toBe(null);
    });
    it('adds, replaces and removes by room id', () => {
        let list = withJoinedRecord([], REC);
        list = withJoinedRecord(list, { ...REC, title: 'Renamed' });
        expect(list).toHaveLength(1);
        expect(list[0].title).toBe('Renamed');
        list = withJoinedRecord(list, { ...REC, roomId: 'room-2' });
        expect(withoutJoinedRecord(list, 'room-1').map(r => r.roomId)).toEqual(['room-2']);
        expect(normalizeJoinedList('nope')).toEqual([]);
    });
    it('scopes the cache keys by WebID', () => {
        expect(joinedCacheKey('https://a/#me')).not.toBe(joinedCacheKey('https://b/#me'));
        expect(joinPendingKey('https://a/#me')).not.toBe(joinedCacheKey('https://a/#me'));
    });
});

describe('joined-room persistence', () => {
    const SELF = 'https://me.example/profile/card#me';
    it('starts the new session without waiting for an old read and rejects its eventual result', async () => {
        let session = 1, release;
        const pending = new Promise(resolve => { release = resolve; });
        const read = vi.fn().mockImplementationOnce(() => pending).mockResolvedValue([]);
        const write = vi.fn(async () => true), storage = { setItem: vi.fn() };
        const store = createJoinedRoomStore({ getWebId: () => SELF, getSessionId: () => session, read, write, storage });
        const old = store.list();
        await vi.waitFor(() => expect(read).toHaveBeenCalledOnce());
        session++;
        expect(await store.list()).toEqual([]);
        release([REC]);
        expect(await old).toEqual([]);
        expect(write).not.toHaveBeenCalled();
        expect(storage.setItem).toHaveBeenCalledTimes(1);
        expect(storage.setItem).toHaveBeenCalledWith(joinedCacheKey(SELF), '[]');
    });
    function harness() {
        let saved = [];
        const cache = new Map();
        const read = vi.fn(async () => structuredClone(saved));
        const write = vi.fn(async (list) => { saved = structuredClone(list); return true; });
        const storage = { getItem: (k) => cache.get(k), setItem: (k, v) => cache.set(k, v) };
        return { read, write, storage, cache, store: createJoinedRoomStore({ read, write, storage, getWebId: () => SELF }) };
    }

    it('restores an approved room from the pod and from cache while offline after reload', async () => {
        const h = harness();
        expect(await h.store.update((list) => withJoinedRecord(list, REC))).toBe(true);
        const reload = createJoinedRoomStore({ ...h, getWebId: () => SELF });
        expect(await reload.list()).toEqual([REC]);
        h.read.mockResolvedValue(null);
        expect(await createJoinedRoomStore({ ...h, getWebId: () => SELF }).list()).toEqual([REC]);
    });

    it('migrates known damaged legacy records from the account cache without losing a new approval', async () => {
        const h = harness();
        h.cache.set(joinedCacheKey(SELF), JSON.stringify([REC]));
        h.read.mockResolvedValueOnce(LEGACY_JOINED_ROOMS_UNREADABLE);
        expect(await h.store.list()).toEqual([REC]);
        h.read.mockResolvedValueOnce(LEGACY_JOINED_ROOMS_UNREADABLE);
        const second = { ...REC, roomId: 'room-2' };
        expect(await h.store.update((list) => withJoinedRecord(list, second))).toBe(true);
        expect(await createJoinedRoomStore({ ...h, getWebId: () => SELF }).list()).toEqual([REC, second]);
    });

    it('retains both rooms when two approvals are saved concurrently', async () => {
        const h = harness();
        const second = { ...REC, roomId: 'room-2' };
        expect(await Promise.all([
            h.store.update((list) => withJoinedRecord(list, REC)),
            h.store.update((list) => withJoinedRecord(list, second)),
        ])).toEqual([true, true]);
        expect(await h.store.list()).toEqual([REC, second]);
    });

    it('does not acknowledge a failed write or replace a list that could not be read', async () => {
        const h = harness();
        h.write.mockResolvedValueOnce(false);
        expect(await h.store.update((list) => withJoinedRecord(list, REC))).toBe(false);
        expect(h.cache.size).toBe(0);
        h.read.mockResolvedValueOnce(null);
        expect(await h.store.update((list) => withJoinedRecord(list, REC))).toBe(false);
        expect(h.write).toHaveBeenCalledTimes(1);
        // A later retry can finish the approval.
        expect(await h.store.update((list) => withJoinedRecord(list, REC))).toBe(true);
    });

    it('keeps a leave authoritative across reload instead of resurrecting the cache', async () => {
        const h = harness();
        await h.store.update((list) => withJoinedRecord(list, REC));
        await h.store.update((list) => withoutJoinedRecord(list, REC.roomId));
        expect(await createJoinedRoomStore({ ...h, getWebId: () => SELF }).list()).toEqual([]);
    });

    it('does not leak an old account cache into a new account', async () => {
        const h = harness();
        await h.store.update((list) => withJoinedRecord(list, REC));
        h.read.mockResolvedValue(null);
        expect(await createJoinedRoomStore({ ...h, getWebId: () => 'https://other.example/#me' }).list()).toEqual([]);
    });
});

// Two independent devices share HTTP resources, not an in-process write queue.
function conditionalRepository(initial = null, { raceReads = false, etag = true } = {}) {
    const root = 'https://me.example/';
    const self = root + 'profile/card#me';
    const resource = root + 'proxion/joined-rooms.json';
    let records = initial == null ? null : structuredClone(initial), revision = 1, reads = 0;
    let release;
    const barrier = new Promise((resolve) => { release = resolve; });
    const response = (status, body = '', tag = null) => ({ ok: status >= 200 && status < 300, status,
        text: async () => body, headers: { get: (key) => key.toLowerCase() === 'etag' ? tag : null } });
    const fetch = vi.fn(async (url, opts = {}) => {
        if (url !== resource) return response(404);
        if (opts.method === 'PUT') {
            const expected = records == null ? '*' : '"' + revision + '"';
            const provided = opts.headers[records == null ? 'If-None-Match' : 'If-Match'];
            if (provided !== expected) return response(412);
            records = JSON.parse(opts.body)['px:rooms'];
            revision++;
            return response(201);
        }
        const snapshot = records == null ? response(404) : response(200,
            JSON.stringify({ 'px:rooms': records }), etag ? '"' + revision + '"' : null);
        if (raceReads && reads++ < 2) { if (reads === 2) release(); await barrier; }
        return snapshot;
    });
    function device({ cached = [], identity = () => self } = {}) {
        const cache = new Map([[joinedCacheKey(self), JSON.stringify(cached)]]);
        const storage = { getItem: (key) => cache.get(key), setItem: (key, value) => cache.set(key, value) };
        const io = createPodIo({ fetch, isLoggedIn: () => true, storageRoot: () => root, webId: identity });
        const readVersion = io.podReadJoinedRoomsVersion, writeVersion = io.podWriteJoinedRoomsVersion;
        return { io, cache, storage, store: createJoinedRoomStore({ readVersion, writeVersion, getWebId: identity, storage }) };
    }
    return { device, fetch, saved: () => structuredClone(records) };
}

describe('joined-room updates across independent devices', () => {
    const SELF = 'https://me.example/profile/card#me';
    const second = { ...REC, roomId: 'room-2' };

    it.each([null, []])('retains simultaneous approvals when both devices read the same version (%j)', async (initial) => {
        const server = conditionalRepository(initial, { raceReads: true });
        const alice = server.device(), otherDevice = server.device();
        expect(await Promise.all([
            alice.store.update((list) => withJoinedRecord(list, REC)),
            otherDevice.store.update((list) => withJoinedRecord(list, second)),
        ])).toEqual([true, true]);
        expect(server.saved().map((r) => r.roomId).sort()).toEqual(['room-1', 'room-2']);
        expect(await server.device().store.list()).toEqual(server.saved());
        const puts = server.fetch.mock.calls.filter(([, opts]) => opts.method === 'PUT');
        expect(puts).toHaveLength(3); // one rejected stale write, then reapply
    });

    it('reapplies a leave against a concurrent approval without reviving departed membership', async () => {
        const server = conditionalRepository([REC], { raceReads: true });
        const leaving = server.device({ cached: [REC] }), joining = server.device({ cached: [REC] });
        expect(await Promise.all([
            leaving.store.update((list) => withoutJoinedRecord(list, REC.roomId)),
            joining.store.update((list) => withJoinedRecord(list, second)),
        ])).toEqual([true, true]);
        expect(server.saved()).toEqual([second]);
        expect(await server.device().store.list()).toEqual([second]);
    });

    it('does not overwrite an existing resource without an exposed strong ETag', async () => {
        const server = conditionalRepository([REC], { etag: false });
        const device = server.device({ cached: [REC] });
        expect(await device.store.update((list) => withJoinedRecord(list, second))).toBe(false);
        expect(server.saved()).toEqual([REC]);
        expect(server.fetch.mock.calls.some(([, opts]) => opts.method === 'PUT')).toBe(false);
    });

    it('bounds repeated conflicts and leaves the account cache intact for retry', async () => {
        const cache = new Map([[joinedCacheKey(SELF), JSON.stringify([REC])]]);
        const readVersion = vi.fn(async () => ({ records: [REC], exists: true, etag: '"1"' }));
        const writeVersion = vi.fn(async () => ({ ok: false, conflict: true }));
        const store = createJoinedRoomStore({ readVersion, writeVersion, getWebId: () => SELF,
            storage: { getItem: (key) => cache.get(key), setItem: (key, value) => cache.set(key, value) }, maxRetries: 3 });
        expect(await store.update((list) => withJoinedRecord(list, second))).toBe(false);
        expect(writeVersion).toHaveBeenCalledTimes(3);
        expect(readVersion).toHaveBeenCalledTimes(3);
        expect(JSON.parse(cache.get(joinedCacheKey(SELF)))).toEqual([REC]);
    });

    it('uses a concurrent canonical migration instead of restoring stale cached records over it', async () => {
        const cache = new Map([[joinedCacheKey(SELF), JSON.stringify([REC])]]);
        const readVersion = vi.fn().mockResolvedValueOnce({ records: LEGACY_JOINED_ROOMS_UNREADABLE, exists: false })
            .mockResolvedValueOnce({ records: [second], exists: true, etag: '"2"' });
        const writeVersion = vi.fn(async () => ({ ok: false, conflict: true }));
        const store = createJoinedRoomStore({ readVersion, writeVersion, getWebId: () => SELF,
            storage: { getItem: (key) => cache.get(key), setItem: (key, value) => cache.set(key, value) } });
        expect(await store.list()).toEqual([second]);
        expect(JSON.parse(cache.get(joinedCacheKey(SELF)))).toEqual([second]);
        expect(writeVersion).toHaveBeenCalledTimes(1);
    });

    it('keeps failed legacy migration retryable without acknowledging an unsaved approval', async () => {
        const readVersion = vi.fn(async () => ({ records: LEGACY_JOINED_ROOMS_UNREADABLE, exists: false }));
        const writeVersion = vi.fn(async () => ({ ok: false, conflict: false }));
        const cache = new Map([[joinedCacheKey(SELF), JSON.stringify([REC])]]);
        const store = createJoinedRoomStore({ readVersion, writeVersion, getWebId: () => SELF,
            storage: { getItem: (key) => cache.get(key), setItem: (key, value) => cache.set(key, value) } });
        expect(await store.list()).toEqual([REC]);
        expect(await store.update((list) => withJoinedRecord(list, second))).toBe(false);
        expect(JSON.parse(cache.get(joinedCacheKey(SELF)))).toEqual([REC]);
        writeVersion.mockResolvedValue({ ok: true, conflict: false });
        expect(await store.update((list) => withJoinedRecord(list, second))).toBe(true);
        expect(JSON.parse(cache.get(joinedCacheKey(SELF)))).toEqual([REC, second]);
    });

    it('stops before a write or retry when the active account changes', async () => {
        let identity = SELF;
        const readVersion = vi.fn(async () => ({ records: [], exists: false }));
        const writeVersion = vi.fn(async () => ({ ok: true, conflict: false }));
        const store = createJoinedRoomStore({ readVersion, writeVersion, getWebId: () => identity });
        expect(await store.update((list) => { identity = 'https://other.example/#me'; return withJoinedRecord(list, REC); })).toBe(false);
        expect(writeVersion).not.toHaveBeenCalled();
        identity = SELF;
        writeVersion.mockImplementationOnce(async () => { identity = 'https://other.example/#me'; return { ok: false, conflict: true }; });
        expect(await store.update((list) => withJoinedRecord(list, REC))).toBe(false);
        expect(readVersion).toHaveBeenCalledTimes(2);
        expect(writeVersion).toHaveBeenCalledTimes(1);
    });
});

function syncHarness(days) {
    const watched = [];
    const reported = [];
    let tick = null;
    const sync = createRoomSync({
        readDay: vi.fn(async (room, date) => days[date.toISOString().slice(0, 10)] || []),
        dayUrl: (room, date) => room.container + date.toISOString().slice(0, 10) + '/chat.ttl',
        watch: vi.fn((url, cb) => { watched.push({ url, cb }); return () => {}; }),
        onNew: (roomId, msgs, info) => reported.push({ roomId, ids: msgs.map(m => m.message_id), initial: info.initial }),
        now: () => Date.parse('2026-10-29T12:00:00Z'),
        setTimer: (fn) => { tick = fn; return 1; },
        clearTimer: () => { tick = null; },
    });
    return { sync, watched, reported, tick: () => tick && tick() };
}
const flush = () => new Promise((r) => setTimeout(r, 0));

describe('createRoomSync', () => {
    it('does not fetch the next day with a new session after a stopped read completes', async () => {
        let release;
        const pending = new Promise(resolve => { release = resolve; });
        const readDay = vi.fn(() => pending), onNew = vi.fn();
        const sync = createRoomSync({ readDay, onNew, dayUrl: () => 'https://pod/day',
            watch: () => () => {}, setTimer: () => 1, clearTimer: vi.fn() });
        sync.track({ roomId: 'old', container: 'https://pod/old/' });
        expect(readDay).toHaveBeenCalledOnce();
        sync.stop(); release([{ message_id: 'old-message' }]);
        await pending; await Promise.resolve();
        expect(readDay).toHaveBeenCalledOnce();
        expect(onNew).not.toHaveBeenCalled();
    });
    it('seeds a room from its recent days, then reports only new ids', async () => {
        const days = {
            '2026-10-28': [{ message_id: 'a', timestamp: '2026-10-28T10:00:00Z' }],
            '2026-10-29': [{ message_id: 'b', timestamp: '2026-10-29T10:00:00Z' }],
        };
        const h = syncHarness(days);
        h.sync.track({ roomId: 'r', container: 'https://p/proxion/rooms/r/', owner: 'https://p/#me' });
        await flush();
        expect(h.reported).toEqual([{ roomId: 'r', ids: ['a', 'b'], initial: true }]);
        days['2026-10-29'].push({ message_id: 'c', timestamp: '2026-10-29T11:00:00Z' });
        await h.sync.refresh('r');
        expect(h.reported[1]).toEqual({ roomId: 'r', ids: ['c'], initial: false });
        await h.sync.refresh('r');
        expect(h.reported).toHaveLength(2);
    });

    it('watches the current day file and reads again on a notification', async () => {
        const days = { '2026-10-29': [] };
        const h = syncHarness(days);
        h.sync.track({ roomId: 'r', container: 'https://p/proxion/rooms/r/' });
        await flush();
        expect(h.watched[0].url).toBe('https://p/proxion/rooms/r/2026-10-29/chat.ttl');
        days['2026-10-29'].push({ message_id: 'n1', timestamp: '2026-10-29T11:00:00Z' });
        h.watched[0].cb();
        await flush();
        expect(h.reported.at(-1)).toEqual({ roomId: 'r', ids: ['n1'], initial: false });
    });

    it('polls every tracked room as a backstop and stops when untracked', async () => {
        const days = { '2026-10-29': [] };
        const h = syncHarness(days);
        h.sync.track({ roomId: 'r', container: 'https://p/proxion/rooms/r/' });
        await flush();
        days['2026-10-29'].push({ message_id: 'p1', timestamp: '2026-10-29T11:00:00Z' });
        h.tick();
        await flush();
        expect(h.reported.at(-1).ids).toEqual(['p1']);
        h.sync.untrack('r');
        expect(h.sync.tracked()).toEqual([]);
    });
});
