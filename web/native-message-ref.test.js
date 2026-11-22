import { describe, it, expect } from 'vitest';
import { nativeMessageRef, resolveNativeMessageRef, validNativeMessageRef } from './native-message-ref.js';
const context = { threadId: 'thread', selfWebId: 'did:key:alice', peerWebId: 'did:key:bob' };
const message = (author, id = 'file-1', thread = 'thread') => ({ message_id: 'native:' + author + ':' + id,
    from_webid: author, thread_id: thread, file: { filename: 'private.txt', size: 20 } });

describe('native attachment references', () => {
    it('separates a wire UUID from each endpoint history key without exposing private filenames', () => {
        const source = message(context.selfWebId), ref = nativeMessageRef(source, context);
        expect(ref).toEqual({ v: 1, kind: 'native_file', id: 'file-1', author: 'actor' });
        const remoteContext = { threadId: 'remote-thread', selfWebId: 'did:key:recipient', peerWebId: 'did:key:gateway' };
        const received = message(remoteContext.peerWebId, 'file-1', remoteContext.threadId);
        expect(resolveNativeMessageRef(ref, { ...remoteContext, actorWebId: remoteContext.peerWebId, messages: [received] })).toBe(received);
        expect(source.message_id).toBe('native:did:key:alice:file-1');
        expect(JSON.stringify(ref)).not.toContain('private.txt');
    });
    it('uses original authorship when both participants choose the same wire UUID', () => {
        const mine = message(context.selfWebId), theirs = message(context.peerWebId);
        const byMe = nativeMessageRef(mine, context), byPeer = nativeMessageRef(theirs, context);
        expect(resolveNativeMessageRef(byMe, { ...context, actorWebId: context.selfWebId, messages: [mine, theirs] })).toBe(mine);
        expect(resolveNativeMessageRef(byPeer, { ...context, actorWebId: context.selfWebId, messages: [mine, theirs] })).toBe(theirs);
        expect(resolveNativeMessageRef({ ...byMe, author: 'peer' }, { ...context, actorWebId: context.peerWebId, messages: [mine, theirs] })).toBe(mine);
    });
    it('rejects a foreign thread, account, actor mismatch and native-looking text id', () => {
        const mine = message(context.selfWebId);
        for (const candidate of [{ ...mine, thread_id: 'foreign' }, { ...mine, from_webid: context.peerWebId },
            { ...mine, file: undefined }, message('did:key:foreign'), { ...mine, message_id: 'file-1' },
            { ...mine, message_id: 'native:' + context.selfWebId + ':../file' }]) {
            expect(nativeMessageRef(candidate, context)).toBeNull();
        }
        expect(nativeMessageRef(mine, { ...context, selfWebId: 'did:key:other' })).toBeNull();
        expect(nativeMessageRef(mine, { ...context, peerWebId: context.selfWebId })).toBeNull();
    });
    it('requires a versioned bounded exact reference shape and rejects prototype/path surprises', () => {
        const good = { v: 1, kind: 'native_file', id: 'file-1', author: 'actor' };
        expect(validNativeMessageRef(good)).toBe(true);
        expect(validNativeMessageRef(Object.assign(Object.create(null), good))).toBe(true);
        for (const patch of [{ v: true }, { v: 2 }, { kind: 'text' }, { author: 'someone' }, { id: '../x' },
            { id: 'a'.repeat(129) }, { id: '__proto__:x' }, { extra: 'field' }, { id: 1 }]) {
            expect(validNativeMessageRef({ ...good, ...patch })).toBe(false);
        }
        expect(validNativeMessageRef(Object.create(good))).toBe(false);
    });
    it('does not guess when the original author/message is absent or ambiguous', () => {
        const mine = message(context.selfWebId), ref = nativeMessageRef(mine, context);
        expect(resolveNativeMessageRef(ref, { ...context, actorWebId: 'did:key:foreign', messages: [mine] })).toBeNull();
        expect(resolveNativeMessageRef(ref, { ...context, actorWebId: context.selfWebId, messages: [mine, { ...mine }] })).toBeNull();
        expect(resolveNativeMessageRef(ref, { ...context, actorWebId: context.selfWebId, messages: [message(context.selfWebId, 'other')] })).toBeNull();
    });
});
