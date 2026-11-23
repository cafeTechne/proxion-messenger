import 'fake-indexeddb/auto';
import { beforeEach, describe, expect, it, vi } from 'vitest';
const state = vi.hoisted(() => ({ account: 'alice' }));
vi.mock('./auth.js', () => ({ accountDbName: name => `${name}:${state.account}` }));
import { dmHistoryApplyNativeEdit, dmHistoryClearAll, dmHistoryGet, dmHistorySaveFile, dmHistorySetEnabled } from './dmhistory.js';
import { encodeNativeEdit } from './native-edit-codec.js';
const source = { threadId: 'thread', selfWebId: 'did:key:alice', peerWebId: 'did:key:bob' };
const context = { ...source, actorWebId: source.selfWebId };
const message = { message_id: 'native:did:key:alice:file', thread_id: 'thread', from_webid: 'did:key:alice', content: 'original',
    file: { filename: 'file.txt', mime_type: 'text/plain', size: 5 } };
function action(sequence = 1, text = 'caption', actionId = `edit-${sequence}`, parent = 'a'.repeat(64)) {
    return {
        plaintext: encodeNativeEdit({ ...message, native_edit_seq: sequence - 1 }, source,
            { sequence, text, actionId, parentHeaderDigest: parent }).plaintext,
        event: { type: 'native_edit', native_edit_v: 1, from_webid: source.selfWebId, message_id: actionId,
            file_id: 'file', native_sequence: sequence, parent_header_digest: parent },
    };
}
async function apply(a, ctx = context, opts) { return dmHistoryApplyNativeEdit(a.plaintext, a.event, ctx, opts); }
beforeEach(async () => {
    state.account = 'alice'; dmHistorySetEnabled(true); await dmHistoryClearAll();
    expect(await dmHistorySaveFile(message, new Blob(['bytes']))).toBe(true);
});
describe('atomic native caption history', () => {
    it('commits caption and exact action together while preserving the file through retries', async () => {
        expect(await apply(action())).toBe(true);
        const row = await dmHistoryGet(message.message_id);
        expect(row).toMatchObject({ content: 'caption', native_edit_seq: 1, native_edit_action: 'edit-1', native_edit_parent: 'a'.repeat(64),
            from_webid: message.from_webid, file: { data_b64: 'Ynl0ZXM=' }, edits: [{ prev_content: 'original' }] });
        expect(await apply(action())).toBe(true);
        expect((await dmHistoryGet(message.message_id)).edits).toHaveLength(1);
        expect(await dmHistorySaveFile(message, new Blob(['bytes']))).toBe(true);
        expect((await dmHistoryGet(message.message_id)).content).toBe('caption');
    });
    it('accepts the authenticated peer alias on the receiving device', async () => {
        expect(await apply(action(), { ...context, selfWebId: source.peerWebId, peerWebId: source.selfWebId })).toBe(true);
    });
    it('rejects nonauthor, unrelated thread, routing substitution and missing original', async () => {
        expect(await apply(action(), { ...context, actorWebId: source.peerWebId })).toBe(false);
        expect(await apply(action(), { ...context, threadId: 'foreign' })).toBe(false);
        expect(await apply({ ...action(), event: { ...action().event, from_webid: source.peerWebId } })).toBe(false);
        await dmHistoryClearAll(); expect(await apply(action())).toBe(false);
        expect(await dmHistoryGet(message.message_id)).toBeNull();
    });
    it('rejects reordered/conflicting actions and retains later captions against old replay', async () => {
        expect(await apply(action(2))).toBe(false); expect(await apply(action())).toBe(true);
        expect(await apply(action(1, 'different'))).toBe(false);
        expect(await apply(action(1, 'caption', 'other'))).toBe(false);
        expect(await apply(action(2, 'later', 'edit-2', 'b'.repeat(64)))).toBe(false);
        expect(await apply(action(2, 'later'))).toBe(true); expect(await apply(action())).toBe(false);
        expect((await dmHistoryGet(message.message_id)).content).toBe('later');
    });
    it('rolls back both caption and marker on write failure, then permits exact retry', async () => {
        const put = IDBObjectStore.prototype.put;
        const spy = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (value, ...args) {
            if (this.name === 'messages' && value.native_edit_seq) { this.transaction.abort(); return; }
            return put.call(this, value, ...args);
        });
        try { expect(await apply(action())).toBe(false); } finally { spy.mockRestore(); }
        const row = await dmHistoryGet(message.message_id);
        expect(row.content).toBe('original'); expect(row.native_edit_seq).toBeUndefined();
        expect(await apply(action())).toBe(true);
    });
    it('requires enabled history and current account/conversation throughout the write', async () => {
        dmHistorySetEnabled(false); expect(await apply(action())).toBe(false); dmHistorySetEnabled(true);
        expect(await apply(action(), context, { current: () => false })).toBe(false);
        let checks = 0;
        expect(await apply(action(), context, { current: () => { if (++checks === 3) state.account = 'bob'; return true; } })).toBe(false);
        state.account = 'alice'; expect((await dmHistoryGet(message.message_id)).content).toBe('original');
    });
});
