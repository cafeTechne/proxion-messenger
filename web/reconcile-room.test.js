// reconcile-room.test.js — PLAN_ROUND_69 D1: on room open the pod is the
// AUTHORITATIVE base list and local un-acked sends overlay on top. Pure-function
// tests for reconcileRoomHistory; the live reconstruction (empty local base,
// edits/deletes) is proven in podcanonical-prototype.test.js.
import { describe, it, expect } from 'vitest';
import { reconcileRoomHistory } from './longchat.js';

const msg = (id, over = {}) => ({
    message_id: id, content: `c-${id}`, from_webid: 'https://me.pod/#me',
    timestamp: `2026-07-26T09:0${id.replace(/\D/g, '') || 0}:00.000Z`, ...over,
});

describe('reconcileRoomHistory (pod-authoritative)', () => {
    it('makes pod content win for a message present in both', () => {
        const local = [msg('1', { content: 'stale local text' })];
        const pod = [msg('1', { content: 'authoritative pod text' })];
        const out = reconcileRoomHistory(local, pod);
        expect(out).toHaveLength(1);
        expect(out[0].content).toBe('authoritative pod text');
    });

    it('keeps local-only richer fields the pod copy lacks', () => {
        const local = [msg('1', { content: 'x', reply_to_id: 'r9', from_display_name: 'Me' })];
        // Pod copy (e.g. from Long Chat) has no reply context and a blank name.
        const pod = [msg('1', { content: 'x', reply_to_id: undefined, from_display_name: '' })];
        const out = reconcileRoomHistory(local, pod);
        expect(out[0].reply_to_id).toBe('r9');          // preserved
        expect(out[0].from_display_name).toBe('Me');     // pod blank did not clobber
    });

    it('overlays a local-only un-acked send the pod does not have yet', () => {
        const local = [msg('1'), msg('pending', { timestamp: '2026-07-26T09:05:00.000Z' })];
        const pod = [msg('1')];
        const out = reconcileRoomHistory(local, pod);
        expect(out.map(m => m.message_id)).toEqual(['1', 'pending']);
    });

    it('surfaces a foreign pod message local has never seen', () => {
        const local = [msg('1')];
        const pod = [msg('1'), msg('foreign', { from_webid: 'https://other.pod/#me' })];
        const out = reconcileRoomHistory(local, pod);
        expect(out.map(m => m.message_id)).toContain('foreign');
    });

    it('drops a tombstoned message, removing a copy shown optimistically from local', () => {
        const local = [msg('1'), msg('2')];
        const pod = [msg('1'), msg('2', { deleted: true, content: '' })];
        const out = reconcileRoomHistory(local, pod);
        expect(out.map(m => m.message_id)).toEqual(['1']);   // 2 withdrawn
    });

    it('returns local unchanged (order preserved) when the pod list is empty', () => {
        // A pod outage / unsynced new room must never blank or reorder a room.
        const local = [msg('1'), msg('2')];
        const out = reconcileRoomHistory(local, []);
        expect(out.map(m => m.message_id)).toEqual(['1', '2']);
        expect(out[0].content).toBe('c-1');
    });

    it('orders the merged result oldest-first by timestamp', () => {
        const local = [msg('b', { timestamp: '2026-07-26T09:30:00.000Z' })];
        const pod = [msg('a', { timestamp: '2026-07-26T09:10:00.000Z' })];
        const out = reconcileRoomHistory(local, pod);
        expect(out.map(m => m.message_id)).toEqual(['a', 'b']);
    });

    it('ignores malformed entries without an id on either side', () => {
        const out = reconcileRoomHistory([null, { content: 'no id' }, msg('1')], [{ deleted: true }]);
        expect(out.map(m => m.message_id)).toEqual(['1']);
    });
});
