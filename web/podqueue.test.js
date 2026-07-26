// podqueue.test.js — PLAN_ROUND_69 D3: the durable offline queue for room pod
// writes. Enqueue on a failed write, replay in send order on reconnect, dedup by
// id, and stop at the first failure so a still-offline device keeps its backlog.
import 'fake-indexeddb/auto';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
    podQueueAdd, podQueueRemove, podQueueList, podQueueCount,
    podQueueClear, podQueueFlush,
} from './podqueue.js';

const entry = (id, over = {}) => ({
    message_id: id, room_id: 'general',
    msg: { content: `c-${id}`, timestamp: `2026-07-26T09:00:0${id.replace(/\D/g, '') || 0}.000Z` },
    ...over,
});

beforeEach(async () => { await podQueueClear(); });

describe('podQueue add / list / remove', () => {
    it('enqueues and lists in send order (queued_at)', async () => {
        await podQueueAdd(entry('a', { queued_at: 100 }));
        await podQueueAdd(entry('b', { queued_at: 50 }));
        await podQueueAdd(entry('c', { queued_at: 150 }));
        expect((await podQueueList()).map(r => r.message_id)).toEqual(['b', 'a', 'c']);
        expect(await podQueueCount()).toBe(3);
    });

    it('dedups by message_id (same id queued twice is one entry)', async () => {
        await podQueueAdd(entry('a', { queued_at: 1 }));
        await podQueueAdd(entry('a', { queued_at: 2 }));
        expect(await podQueueCount()).toBe(1);
    });

    it('ignores malformed entries', async () => {
        await podQueueAdd(null);
        await podQueueAdd({ message_id: 'x' });      // no room_id
        await podQueueAdd({ room_id: 'general' });    // no id
        expect(await podQueueCount()).toBe(0);
    });

    it('removes a single entry by id', async () => {
        await podQueueAdd(entry('a'));
        await podQueueAdd(entry('b'));
        await podQueueRemove('a');
        expect((await podQueueList()).map(r => r.message_id)).toEqual(['b']);
    });
});

describe('podQueueFlush', () => {
    it('replays in order and removes each entry the write accepts', async () => {
        await podQueueAdd(entry('a', { queued_at: 1 }));
        await podQueueAdd(entry('b', { queued_at: 2 }));
        const seen = [];
        const res = await podQueueFlush(async (row) => { seen.push(row.message_id); return true; });
        expect(seen).toEqual(['a', 'b']);            // send order
        expect(res).toEqual({ flushed: 2, remaining: 0 });
        expect(await podQueueCount()).toBe(0);
    });

    it('stops at the first failure and keeps the ordered backlog', async () => {
        await podQueueAdd(entry('a', { queued_at: 1 }));
        await podQueueAdd(entry('b', { queued_at: 2 }));
        await podQueueAdd(entry('c', { queued_at: 3 }));
        // 'a' succeeds, 'b' fails (still offline): 'b' and 'c' remain, in order.
        const res = await podQueueFlush(async (row) => row.message_id === 'a');
        expect(res.flushed).toBe(1);
        expect(res.remaining).toBe(2);
        expect((await podQueueList()).map(r => r.message_id)).toEqual(['b', 'c']);
    });

    it('treats a throwing write as a failure and keeps the entry', async () => {
        await podQueueAdd(entry('a'));
        const res = await podQueueFlush(async () => { throw new Error('offline'); });
        expect(res.flushed).toBe(0);
        expect(await podQueueCount()).toBe(1);
    });

    it('fires onFlushed for each successfully replayed id', async () => {
        await podQueueAdd(entry('a', { queued_at: 1 }));
        await podQueueAdd(entry('b', { queued_at: 2 }));
        const cleared = [];
        await podQueueFlush(async () => true, (id) => cleared.push(id));
        expect(cleared).toEqual(['a', 'b']);
    });

    it('does not overlap: a flush already running yields to the caller', async () => {
        await podQueueAdd(entry('a'));
        let release;
        const gate = new Promise(r => { release = r; });
        const first = podQueueFlush(async () => { await gate; return true; });
        const second = await podQueueFlush(async () => true);   // runs while first is mid-flight
        expect(second.flushed).toBe(0);                          // yielded, did nothing
        release(true);
        await first;
        expect(await podQueueCount()).toBe(0);                   // first still completed
    });

    it('is a no-op without a write function', async () => {
        await podQueueAdd(entry('a'));
        const res = await podQueueFlush(null);
        expect(res.flushed).toBe(0);
        expect(await podQueueCount()).toBe(1);
    });
});
