// longchat-order.test.js — PLAN_ROUND_69 D4 (read side): a per-message px:seq
// order hint, preferred over timestamp so a user's devices agree on order despite
// client clock skew. Pure-function tests; the live round trip is in
// podcanonical-d4.test.js.
import { describe, it, expect } from 'vitest';
import {
    compareByOrder, buildSeqPatch, buildAppendPatch, parseLongChatJsonLd,
    reconcileRoomHistory, P,
} from './longchat.js';

describe('compareByOrder', () => {
    it('orders by seq when BOTH messages have one', () => {
        const a = { message_id: 'a', seq: 200, timestamp: '2026-07-26T09:00:00Z' };
        const b = { message_id: 'b', seq: 100, timestamp: '2026-07-26T10:00:00Z' };
        expect([a, b].sort(compareByOrder).map(m => m.message_id)).toEqual(['b', 'a']);
    });

    it('falls back to timestamp when a seq is missing on either side', () => {
        const a = { message_id: 'a', seq: 200, timestamp: '2026-07-26T09:00:00Z' };
        const b = { message_id: 'b', timestamp: '2026-07-26T08:00:00Z' };   // no seq
        expect([a, b].sort(compareByOrder).map(m => m.message_id)).toEqual(['b', 'a']);
    });
});

describe('buildSeqPatch', () => {
    it('emits an INSERT DATA adding px:seq as an integer', () => {
        const iri = 'https://me.pod/proxion/rooms/general/2026/07/26/chat.ttl#m1';
        const patch = buildSeqPatch({ messageIri: iri, seq: 1730000000000 });
        expect(patch).toContain('INSERT DATA {');
        expect(patch).toContain(`<${iri}> <${P.seq}> 1730000000000 .`);
        expect(P.seq).toBe('https://proxion.dev/vocab/v1#seq');
    });

    it('is empty for a non-finite seq (nothing to write)', () => {
        expect(buildSeqPatch({ messageIri: 'x', seq: undefined })).toBe('');
        expect(buildSeqPatch({ messageIri: 'x', seq: NaN })).toBe('');
    });
});

describe('buildAppendPatch with a seq', () => {
    it('includes px:seq when provided and omits it otherwise', () => {
        const base = { channelIri: 'https://p/index.ttl#this', messageIri: 'https://p/d/chat.ttl#m',
            content: 'hi', createdIso: '2026-07-26T09:00:00.000Z', makerIri: 'https://me/#me' };
        expect(buildAppendPatch({ ...base, seq: 42 })).toContain(`<${P.seq}> 42 .`);
        expect(buildAppendPatch(base)).not.toContain(P.seq);
    });
});

describe('parseLongChatJsonLd reads px:seq', () => {
    const node = (seq) => [{
        '@id': 'https://p/d/chat.ttl#m1',
        [P.content]: [{ '@value': 'hello' }],
        [P.created]: [{ '@value': '2026-07-26T09:00:00.000Z', '@type': P.dateTime }],
        ...(seq == null ? {} : { [P.seq]: [{ '@value': seq }] }),
    }];

    it('extracts a numeric seq when present', () => {
        const [m] = parseLongChatJsonLd(node(1730000000000), 'general');
        expect(m.seq).toBe(1730000000000);
    });

    it('leaves seq undefined (not NaN) when absent, so timestamp order is used', () => {
        const [m] = parseLongChatJsonLd(node(null), 'general');
        expect('seq' in m).toBe(false);
    });
});

describe('reconcileRoomHistory prefers seq order', () => {
    it('orders pod messages by seq even when their timestamps disagree', () => {
        // Intended A then B, but A has the LATER clock time (skew). Server seq fixes it.
        const pod = [
            { message_id: 'A', content: 'first', seq: 1000, timestamp: '2026-07-26T09:05:00Z' },
            { message_id: 'B', content: 'second', seq: 2000, timestamp: '2026-07-26T09:01:00Z' },
        ];
        const out = reconcileRoomHistory([], pod);
        expect(out.map(m => m.message_id)).toEqual(['A', 'B']);   // seq order, not timestamp
    });

    it("adopts the pod's seq for a message the local copy lacked one for", () => {
        const local = [{ message_id: 'A', content: 'x', timestamp: '2026-07-26T09:05:00Z' }];
        const pod = [{ message_id: 'A', content: 'x', seq: 1000, timestamp: '2026-07-26T09:05:00Z' }];
        expect(reconcileRoomHistory(local, pod)[0].seq).toBe(1000);
    });
});
