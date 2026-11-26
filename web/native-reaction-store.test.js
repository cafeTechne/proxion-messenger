import 'fake-indexeddb/auto';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createNativeReactionStore } from './native-reaction-store.js';
const context = { threadId: 'thread', selfWebId: 'did:key:alice', peerWebId: 'did:key:bob', actorWebId: 'did:key:bob' };
const action = { mid: 'add-1', ref: { v: 1, kind: 'native_file', id: 'same', author: 'actor' }, tok: '~abcdefghijk', emoji: '👍', seq: 1, op: 'add' };
let scope, databaseName, store;
beforeEach(() => { scope = 'alice-device'; databaseName = `test-native-reactions-${crypto.randomUUID()}`; store = createNativeReactionStore({ getScope: () => scope, databaseName }); });
describe('durable native reaction state', () => {
    it('retains a missing-original action across reopen without creating a message or file', async () => {
        expect(await store.apply(action, context)).toBe(true);
        store = createNativeReactionStore({ getScope: () => scope, databaseName });
        expect(await store.rows(context)).toEqual([{ threadId: 'thread', target: 'native:did:key:bob:same', actorWebId: context.actorWebId,
            mid: action.mid, tok: action.tok, emoji: action.emoji, seq: 1, op: 'add' }]);
    });
    it('keeps remove tombstones across delayed adds, duplicates and reopen, and allows a newer re-add', async () => {
        const removed = { ...action, mid: 'remove-2', seq: 2, op: 'remove' };
        expect(await store.apply(removed, context)).toBe(true);
        expect(await store.apply(action, context)).toBe(true);
        expect(await store.apply(removed, context)).toBe(true);
        expect((await store.rows(context))[0].op).toBe('remove');
        expect(await store.apply({ ...action, mid: 'add-3', seq: 3 }, context)).toBe(true);
        expect((await store.rows(context))[0].seq).toBe(3);
    });
    it('rejects token substitution and same-sequence conflicts without changing retained state', async () => {
        await store.apply(action, context);
        expect(await store.apply({ ...action, tok: '~ABCDEFGHIJK', seq: 2 }, context)).toBe(false);
        expect(await store.apply({ ...action, mid: 'different' }, context)).toBe(false);
        expect(await store.apply({ ...action, op: 'remove' }, context)).toBe(false);
        expect((await store.rows(context))[0].op).toBe('add');
    });
    it('separates author collisions, actors, threads and accounts', async () => {
        await store.apply(action, context);
        await store.apply({ ...action, ref: { ...action.ref, author: 'peer' } }, context);
        await store.apply(action, { ...context, actorWebId: context.selfWebId });
        expect(await store.rows(context)).toHaveLength(3);
        expect(await store.rows({ ...context, threadId: 'other' })).toEqual([]);
        scope = 'other-account'; expect(await store.rows(context)).toEqual([]);
        expect(await store.apply(action, { ...context, actorWebId: 'did:key:mallory' })).toBe(false);
    });
    it('returns failure on aborted writes and permits the identical retry', async () => {
        const original = IDBObjectStore.prototype.put;
        const spy = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (...args) {
            if (this.name === 'actions') { this.transaction.abort(); return; }
            return original.apply(this, args);
        });
        try { expect(await store.apply(action, context)).toBe(false); } finally { spy.mockRestore(); }
        expect(await store.rows(context)).toEqual([]);
        expect(await store.apply(action, context)).toBe(true);
    });
    it('does not commit under a changed account or stale generation', async () => {
        expect(await store.apply(action, context, () => false)).toBe(false);
        let checks = 0;
        expect(await store.apply(action, context, () => { if (++checks === 3) scope = 'changed'; return true; })).toBe(false);
        scope = 'alice-device'; expect(await store.rows(context)).toEqual([]);
    });
    it('fails closed at capacity while retaining tombstones and allowing existing rows to advance', async () => {
        for (let n = 0; n < 2000; n++) expect(await store.apply({ ...action, ref: { ...action.ref, id: `file-${n}` }, op: 'remove' }, context)).toBe(true);
        expect(await store.apply(action, context)).toBe(false);
        expect(await store.apply({ ...action, ref: { ...action.ref, id: 'file-0' }, mid: 'later', seq: 2 }, context)).toBe(true);
        expect(await store.rows(context)).toHaveLength(2000);
    });
});
