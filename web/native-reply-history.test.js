import 'fake-indexeddb/auto';
import { beforeEach, describe, expect, it, vi } from 'vitest';
const state = vi.hoisted(() => ({ account: 'alice' }));
vi.mock('./auth.js', () => ({ accountDbName: name => `${name}:native-reply:${state.account}` }));
import { dmHistorySave, dmHistoryGet, dmHistoryClearAll, dmHistoryResolveNativeReply, dmHistorySetEnabled } from './dmhistory.js';
const context = { threadId: 'thread', selfWebId: 'did:key:alice', peerWebId: 'did:key:bob', actorWebId: 'did:key:alice' };
const reply = { message_id: 'reply', thread_id: 'thread', from_webid: context.actorWebId, content: 'reply text',
    native_reply_ref: { v: 1, kind: 'native_file', id: 'file', author: 'peer' }, native_reply_actor: context.actorWebId, native_reply_unavailable: true };
const file = actor => ({ message_id: `native:${actor}:file`, thread_id: 'thread', from_webid: actor, content: 'file',
    file: { filename: 'file.txt', size: 1, data_b64: 'eA==' } });
beforeEach(async () => { state.account = 'alice'; dmHistorySetEnabled(true); await dmHistoryClearAll(); });
describe('retained native reply references', () => {
    it('preserves a pending reply reference through reload and resolves a delayed original without changing delivery state', async () => {
        expect(await dmHistorySave(reply, { delivery: 'pending', outbox: true })).toBe(true);
        expect(await dmHistoryResolveNativeReply(reply.message_id, context)).toMatchObject({ reply_to_id: null, native_reply_unavailable: true });
        expect(await dmHistoryGet('native:did:key:bob:file')).toBeNull();
        await dmHistorySave(file(context.selfWebId)); await dmHistorySave(file(context.peerWebId));
        expect(await dmHistoryResolveNativeReply(reply.message_id, context)).toMatchObject({ reply_to_id: 'native:did:key:bob:file', native_reply_unavailable: false });
        expect(await dmHistoryGet(reply.message_id)).toMatchObject({ content: reply.content, delivery: 'pending', outbox_id: 'reply', native_reply_ref: reply.native_reply_ref });
    });
    it('does not link a different conversation, actor or account', async () => {
        await dmHistorySave(reply); await dmHistorySave(file(context.peerWebId));
        expect(await dmHistoryResolveNativeReply('reply', { ...context, threadId: 'foreign' })).toBe(false);
        expect(await dmHistoryResolveNativeReply('reply', { ...context, actorWebId: context.peerWebId })).toBe(false);
        state.account = 'other'; expect(await dmHistoryResolveNativeReply('reply', context)).toBe(false);
        expect(await dmHistoryGet('reply')).toBeNull();
    });
    it('rolls back a reference update after write failure and safely permits retry', async () => {
        await dmHistorySave(reply); await dmHistorySave(file(context.peerWebId));
        const original = IDBObjectStore.prototype.put;
        const spy = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (row, ...args) {
            if (row.message_id === 'reply' && row.reply_to_id) { this.transaction.abort(); return; }
            return original.call(this, row, ...args);
        });
        try { expect(await dmHistoryResolveNativeReply('reply', context)).toBe(false); } finally { spy.mockRestore(); }
        expect((await dmHistoryGet('reply')).reply_to_id).toBeNull();
        expect((await dmHistoryResolveNativeReply('reply', context)).native_reply_unavailable).toBe(false);
    });
});
