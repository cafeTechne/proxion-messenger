import 'fake-indexeddb/auto';
import { beforeEach, describe, expect, it, vi } from 'vitest';
const state = vi.hoisted(() => ({ account: '' }));
vi.mock('./auth.js', () => ({ accountDbName: name => `${name}:native-actions:${state.account}` }));
import { encodeNativeAction, decodeNativeAction, isNativeActionPlain } from './native-action-codec.js';
import { dmHistorySave, dmHistorySaveFile, dmHistoryGet, dmHistorySetEnabled, dmHistoryApplyNativeAction,
    dmHistoryDeleteNativeLocal, dmHistoryNativeState, dmHistoryApplyNativeEdit } from './dmhistory.js';
import { encodeNativeEdit } from './native-edit-codec.js';
const context = { threadId: 'thread', selfWebId: 'did:key:alice', peerWebId: 'did:key:bob', actorWebId: 'did:key:alice' };
const file = actor => ({ message_id: `native:${actor}:same`, thread_id: 'thread', from_webid: actor, content: 'private filename',
    file: { filename: 'secret.txt', mime_type: 'text/plain', size: 1, data_b64: 'eA==' } });
const action = (op, clock = 1, mid = 'action', author = 'actor') => ({ mid, ref: { v: 1, kind: 'native_file', id: 'same', author }, op, clock });
const raw = async store => new Promise((resolve, reject) => {
    const req = indexedDB.open(`proxion-dm-history:native-actions:${state.account}`, 3);
    req.onsuccess = () => { const db = req.result, get = db.transaction(store).objectStore(store).getAll(); get.onsuccess = () => { db.close(); resolve(get.result); }; };
    req.onerror = () => reject(req.error);
});
beforeEach(() => { state.account = crypto.randomUUID(); dmHistorySetEnabled(true); });
describe('native action wire authorisation', () => {
    it('permits either file author for pins, but only the authenticated original author for deletion', () => {
        for (const who of [context.selfWebId, context.peerWebId]) {
            expect(decodeNativeAction(encodeNativeAction(file(who), context, { messageId: 'action', operation: 'pin', clock: 1 }).plaintext, 'action')).toBeTruthy();
        }
        expect(() => encodeNativeAction(file(context.peerWebId), context, { messageId: 'action', operation: 'delete', clock: 1 })).toThrow();
        const { plaintext } = encodeNativeAction(file(context.selfWebId), context, { messageId: 'action', operation: 'delete', clock: 1 });
        expect(plaintext).not.toContain('secret.txt'); expect(plaintext).not.toContain('native:');
        expect(decodeNativeAction(plaintext, 'other')).toBeNull();
        expect(decodeNativeAction(plaintext.replace('"v":1', '"v":2'), 'action')).toBeNull();
        expect(isNativeActionPlain(plaintext.replace('"v":1', '"v":2'))).toBe(true);
    });
});
describe('atomic native deletion and pin state', () => {
    it('atomically removes the original and Blob, retaining only bounded replay evidence', async () => {
        const original = file(context.selfWebId); await dmHistorySaveFile(original, new Blob(['x']));
        expect(await raw('native_files')).toHaveLength(1);
        expect(await dmHistoryApplyNativeAction(action('delete'), context)).toBe(true);
        expect(await dmHistoryGet(original.message_id)).toBeNull(); expect(await raw('native_files')).toEqual([]);
        expect((await dmHistoryNativeState(context))[0]).toMatchObject({ message_id: original.message_id, deletedEveryone: true });
        expect(JSON.stringify(await raw('native_state'))).not.toContain('secret.txt');
        expect(await dmHistorySave(original, { requireFile: true })).toBe(true);
        expect(await dmHistorySaveFile(original, new Blob(['x']))).toBe(true);
        expect(await dmHistoryGet(original.message_id)).toBeNull(); expect(await raw('native_files')).toEqual([]);
    });
    it('keeps delete-for-me local, retains account boundaries and permits removal with history disabled', async () => {
        const original = file(context.peerWebId); await dmHistorySave(original);
        dmHistorySetEnabled(false);
        expect(await dmHistoryDeleteNativeLocal(original.message_id, context)).toBe(true);
        expect((await dmHistoryNativeState(context))[0]).toMatchObject({ deletedLocal: true });
        expect((await dmHistoryNativeState(context))[0].deletedEveryone).toBeUndefined();
        dmHistorySetEnabled(true); await dmHistorySave(original); expect(await dmHistoryGet(original.message_id)).toBeNull();
        state.account = 'different'; await dmHistorySave(original); expect(await dmHistoryGet(original.message_id)).toBeTruthy();
    });
    it('does not delete the other author sharing a UUID, or accept foreign actors and threads', async () => {
        await dmHistorySave(file(context.selfWebId)); await dmHistorySave(file(context.peerWebId));
        expect(await dmHistoryApplyNativeAction(action('delete', 1, 'bad', 'peer'), context)).toBe(false);
        expect(await dmHistoryApplyNativeAction(action('delete'), { ...context, actorWebId: 'did:key:mallory' })).toBe(false);
        expect(await dmHistoryApplyNativeAction(action('delete'), { ...context, threadId: 'foreign' })).toBe(false);
        expect(await dmHistoryApplyNativeAction(action('delete'), context)).toBe(true);
        expect(await dmHistoryGet(file(context.peerWebId).message_id)).toBeTruthy();
    });
    it('rolls back tombstone, history and Blob together on write failure, allowing an exact retry', async () => {
        const original = file(context.selfWebId); await dmHistorySaveFile(original, new Blob(['x']));
        const put = IDBObjectStore.prototype.put;
        const spy = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (...args) {
            if (this.name === 'native_state') { this.transaction.abort(); return; } return put.apply(this, args);
        });
        try { expect(await dmHistoryApplyNativeAction(action('delete'), context)).toBe(false); } finally { spy.mockRestore(); }
        expect(await dmHistoryGet(original.message_id)).toBeTruthy(); expect(await raw('native_files')).toHaveLength(1);
        expect(await dmHistoryNativeState(context)).toEqual([]);
        expect(await dmHistoryApplyNativeAction(action('delete'), context)).toBe(true);
    });
    it('consumes delayed authenticated edits without resurrecting a deleted original', async () => {
        const original = file(context.selfWebId), parent = 'a'.repeat(64);
        const encoded = encodeNativeEdit(original, context, { actionId: 'edit', text: 'later caption', sequence: 1, parentHeaderDigest: parent });
        await dmHistoryApplyNativeAction(action('delete'), context);
        expect(await dmHistoryApplyNativeEdit(encoded.plaintext, { type: 'native_edit', native_edit_v: 1, message_id: 'edit',
            file_id: 'same', native_sequence: 1, parent_header_digest: parent, from_webid: context.selfWebId }, context)).toBe(true);
        expect(await dmHistoryGet(original.message_id)).toBeNull();
    });
    it('converges concurrent pins by clock/action id across actor aliases and preserves unpin against stale replay', async () => {
        const a = action('pin', 1, 'a'), b = action('unpin', 1, 'b', 'peer');
        const peer = { ...context, actorWebId: context.peerWebId };
        await dmHistoryApplyNativeAction(b, peer); await dmHistoryApplyNativeAction(a, context);
        expect((await dmHistoryNativeState(context))[0].pin).toMatchObject({ active: false, mid: 'b' });
        await dmHistoryApplyNativeAction(action('pin', 2, 'c'), context);
        expect((await dmHistoryNativeState(context))[0].pin.active).toBe(true);
        expect(await dmHistoryApplyNativeAction(action('unpin', 2, 'c'), context)).toBe(false);
        await dmHistoryApplyNativeAction(action('delete'), context);
        await dmHistoryApplyNativeAction(action('pin', 3, 'd'), context);
        expect((await dmHistoryNativeState(context))[0]).toMatchObject({ deletedEveryone: true, pin: { active: false } });
    });
    it('retains delayed-original pins without fabricating a message and rejects stale scopes', async () => {
        expect(await dmHistoryApplyNativeAction(action('pin'), context)).toBe(true);
        expect(await dmHistoryGet(file(context.selfWebId).message_id)).toBeNull();
        expect((await dmHistoryNativeState(context))[0].pin.active).toBe(true);
        await dmHistorySave(file(context.selfWebId)); expect(await dmHistoryGet(file(context.selfWebId).message_id)).toBeTruthy();
        expect(await dmHistoryApplyNativeAction(action('delete'), context, { current: () => false })).toBe(false);
    });
    it('never resurrects bytes when a Blob save races a deletion', async () => {
        const original = file(context.selfWebId);
        const outcomes = await Promise.all([dmHistorySaveFile(original, new Blob(['x'])), dmHistoryApplyNativeAction(action('delete'), context)]);
        expect(outcomes).toEqual([true, true]); expect(await dmHistoryGet(original.message_id)).toBeNull(); expect(await raw('native_files')).toEqual([]);
    });
    it('bounds retained state without evicting deletion evidence and permits existing-state progress', async () => {
        for (let n = 0; n < 2000; n++) expect(await dmHistoryApplyNativeAction({ ...action('delete'), ref: { ...action('delete').ref, id: `file-${n}` } }, context)).toBe(true);
        expect(await dmHistoryApplyNativeAction(action('pin'), context)).toBe(false);
        expect(await dmHistoryApplyNativeAction({ ...action('delete', 2, 'retry'), ref: { ...action('delete').ref, id: 'file-0' } }, context)).toBe(true);
        expect(await dmHistoryNativeState(context)).toHaveLength(2000);
    });
    it('upgrades v2 history without changing existing messages or file bytes', async () => {
        const original = file(context.selfWebId);
        await new Promise((resolve, reject) => {
            const request = indexedDB.open(`proxion-dm-history:native-actions:${state.account}`, 2);
            request.onupgradeneeded = () => {
                const db = request.result;
                db.createObjectStore('messages', { keyPath: 'message_id' }).createIndex('thread_id', 'thread_id');
                db.createObjectStore('native_files', { keyPath: 'message_id' }).createIndex('size', 'size');
                request.transaction.objectStore('messages').put(original);
            };
            request.onsuccess = () => { request.result.close(); resolve(); }; request.onerror = () => reject(request.error);
        });
        expect(await dmHistoryGet(original.message_id)).toMatchObject(original);
        expect(await dmHistoryNativeState(context)).toEqual([]);
        expect(await dmHistoryApplyNativeAction(action('pin'), context)).toBe(true);
        expect(await dmHistoryGet(original.message_id)).toMatchObject(original);
    });

});
