import { describe, it, expect, vi } from 'vitest';
import {
    createRoomSync, normalizeJoinedRecord, normalizeJoinedList, withJoinedRecord, withoutJoinedRecord,
    joinedCacheKey, joinPendingKey,
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
