import { describe, expect, it, vi } from 'vitest';
import { createNativeReferences } from './native-references.js';
import { encodeNativeAction } from './native-action-codec.js';
import { encodeNativeReaction } from './native-reaction-codec.js';
import { encodeNativeReply } from './native-reply-codec.js';
const context = { threadId: 'thread', selfWebId: 'did:key:alice', peerWebId: 'did:key:bob' };
const original = { message_id: 'native:did:key:bob:file', thread_id: 'thread', from_webid: 'did:key:bob', file: { filename: 'private.txt' } };
function harness(options = {}) {
    let scope = 'account', generation = 0, pub = 'A'.repeat(43);
    const send = vi.fn(() => true), getMessage = vi.fn(async () => original);
    const controller = createNativeReferences({ getScope: () => scope, getGeneration: () => generation, isActive: () => true,
        send, getPeerPub: () => pub, getOwnPub: () => pub, contextForView: () => context,
        contextForMessage: message => ({ ...context, actorWebId: message.from_webid }), getMessage, ...options });
    return { controller, send, getMessage, switchAccount: () => { scope = 'other'; generation++; }, changeKey: () => { pub = 'B'.repeat(43); } };
}
function result(packet, patch = {}) {
    return { type: 'native_ref_capability_result', native_ref_v: 1, request_id: packet.request_id,
        file_id: 'file', author: 'peer', peer_webid: context.peerWebId, expected_pub: 'A'.repeat(43),
        supported: true, primary_device_id: 'did:key:bob-primary', peer_pub: 'A'.repeat(43), ...patch };
}
describe('native reference client capability and receive boundary', () => {
    it('validates local scope before preflight and binds the result to original author, peer and key', async () => {
        const h = harness(); h.controller.start(1);
        await expect(h.controller.prepareReply({ ...original, thread_id: 'foreign' }, { id: 'thread' }, { messageId: 'reply', text: 'reply' })).rejects.toThrow();
        expect(h.send).not.toHaveBeenCalled();
        const pending = h.controller.prepareReply(original, { id: 'thread' }, { messageId: 'reply', text: 'reply' });
        const packet = h.send.mock.calls.at(-1)[0];
        let settled = false; pending.then(() => { settled = true; });
        h.controller.handleEvent(result(packet, { author: 'actor' })); await Promise.resolve(); expect(settled).toBe(false);
        h.controller.handleEvent(result(packet));
        expect(await pending).toMatchObject({ primaryDeviceId: 'did:key:bob-primary', peerPub: 'A'.repeat(43), body: { ref: { author: 'peer' } } });
        h.controller.stop();
    });
    it('does not allow a stale result to spend a header after account/key changes', async () => {
        for (const change of ['switchAccount', 'changeKey']) {
            const h = harness(); h.controller.start(1);
            const pending = h.controller.prepareReply(original, { id: 'thread' }, { messageId: 'reply', text: 'reply' });
            const rejected = expect(pending).rejects.toThrow('Native reference peer unavailable');
            h[change](); h.controller.handleEvent(result(h.send.mock.calls.at(-1)[0])); h.controller.stop(); await rejected;
        }
    });
    it('resolves a reply to the correct cached author, while missing originals retain only reference metadata', async () => {
        const h = harness(); h.controller.start(1);
        // The sender is Alice, replying to Bob's attachment (the peer role).
        const encoded = encodeNativeReply(original, context, { messageId: 'reply', text: 'reply text' });
        const message = { message_id: 'reply', thread_id: 'thread', from_webid: context.selfWebId,
            _native_ref_v: 1, _nativeRefPub: 'A'.repeat(43), content: encoded.plaintext };
        expect(await h.controller.transform(message, () => true)).toMatchObject({ content: 'reply text', reply_to_id: original.message_id, native_reply_unavailable: false });
        h.getMessage.mockResolvedValueOnce(null);
        const missing = await h.controller.transform(message, () => true);
        expect(missing.reply_to_id).toBeNull(); expect(missing.native_reply_unavailable).toBe(true); expect(missing.file).toBeUndefined();
        expect(await h.controller.transform({ ...message, _native_ref_v: undefined }, () => true)).toBeNull();
        h.controller.stop();
    });
    it('withholds consumption after a key/account change during local reference lookup', async () => {
        for (const change of ['switchAccount', 'changeKey']) {
            const h = harness(); h.controller.start(1);
            h.getMessage.mockImplementationOnce(async () => { h[change](); return original; });
            const encoded = encodeNativeReply(original, context, { messageId: 'reply', text: 'reply' });
            expect(await h.controller.transform({ message_id: 'reply', from_webid: context.selfWebId, _native_ref_v: 1,
                _nativeRefPub: 'A'.repeat(43), content: encoded.plaintext }, () => true)).toBeNull(); h.controller.stop();
        }
    });
});

describe('native reaction receive boundary', () => {
    const encoded = encodeNativeReaction(original, context, { messageId: 'react', token: '~abcdefghijk', emoji: '👍', sequence: 1, add: true });
    const message = { message_id: 'react', thread_id: 'thread', from_webid: context.selfWebId,
        _native_ref_v: 1, _nativeRefPub: 'A'.repeat(43), content: encoded.plaintext };
    it('requires durable state before consumption, without looking up or creating a file', async () => {
        const acceptReaction = vi.fn(async () => false), h = harness({ acceptReaction }); h.controller.start(1);
        expect(await h.controller.transform(message, () => true)).toBeNull();
        expect(h.getMessage).not.toHaveBeenCalled();
        acceptReaction.mockResolvedValueOnce(true);
        expect(await h.controller.transform(message, () => true)).toBe(message);
        expect(acceptReaction).toHaveBeenCalledWith(expect.objectContaining({ tok: '~abcdefghijk', op: 'add' }),
            expect.objectContaining({ actorWebId: context.selfWebId }), expect.any(Function));
        expect(await h.controller.transform({ ...message, _native_ref_v: undefined }, () => true)).toBeNull();
        h.controller.stop();
    });
    it('withholds consumption when the account changes during the state write', async () => {
        const h = harness({ acceptReaction: async () => { h.switchAccount(); return true; } }); h.controller.start(1);
        expect(await h.controller.transform(message, () => true)).toBeNull(); h.controller.stop();
    });
    it('preflights reactions using the original author and freezes the authenticated primary key', async () => {
        const h = harness(); h.controller.start(1);
        const pending = h.controller.prepareReaction(original, { id: 'thread' }, { messageId: 'react', token: '~abcdefghijk', emoji: '👍', sequence: 1, add: true });
        h.controller.handleEvent(result(h.send.mock.calls.at(-1)[0]));
        expect(await pending).toMatchObject({ primaryDeviceId: 'did:key:bob-primary', peerPub: 'A'.repeat(43), body: { mid: 'react', ref: { author: 'peer' } } });
        h.controller.stop();
    });
});

describe('native delete/pin separate capability', () => {
    it('does not preflight new actions through a reply-only connection', async () => {
        const h = harness(); h.controller.start(1);
        await expect(h.controller.prepareAction(original, { id: 'thread' }, { messageId: 'pin', operation: 'pin', clock: 1 })).rejects.toThrow();
        expect(h.send).not.toHaveBeenCalled(); expect(h.controller.actionsAvailable()).toBe(false);
    });
    it('requires an action-capable primary proof before returning a prepared pin', async () => {
        const h = harness(); h.controller.start(1, 1);
        const pending = h.controller.prepareAction(original, { id: 'thread' }, { messageId: 'pin', operation: 'pin', clock: 1 });
        const packet = h.send.mock.calls.at(-1)[0]; expect(packet.native_action_v).toBe(1);
        h.controller.handleEvent(result(packet, { native_action_v: 1 }));
        expect(await pending).toMatchObject({ body: { op: 'pin', ref: { author: 'peer' } }, peerPub: 'A'.repeat(43) }); h.controller.stop();
    });
    it('requires the new wire flag, authenticated routing and durable action state before consumption', async () => {
        const acceptAction = vi.fn(async () => false), h = harness({ acceptAction }); h.controller.start(1, 1);
        const encoded = encodeNativeAction(original, context, { messageId: 'pin', operation: 'pin', clock: 1 });
        const message = { message_id: 'pin', thread_id: 'thread', from_webid: context.selfWebId,
            _native_ref_v: 1, _nativeRefPub: 'A'.repeat(43), _nativeActionV: 1, content: encoded.plaintext };
        expect(await h.controller.transform(message, () => true)).toBeNull();
        expect(await h.controller.transform({ ...message, _nativeActionV: undefined }, () => true)).toBeNull();
        acceptAction.mockResolvedValueOnce(true);
        expect(await h.controller.transform(message, () => true)).toBe(message); expect(h.getMessage).not.toHaveBeenCalled();
        h.controller.start(1); expect(await h.controller.transform(message, () => true)).toBeNull(); h.controller.stop();
    });
    it('preflights a retained unpin by its validated author/UUID without a synthetic file', async () => {
        const h = harness(); h.controller.start(1, 1);
        const pending = h.controller.prepareRetainedUnpin({ v: 1, kind: 'native_file', id: 'file', author: 'peer' }, { id: 'thread' }, { messageId: 'unpin', clock: 2 });
        h.controller.handleEvent(result(h.send.mock.calls.at(-1)[0], { native_action_v: 1 }));
        expect(await pending).toMatchObject({ body: { op: 'unpin', clock: 2, ref: { author: 'peer' } } });
        expect(h.getMessage).not.toHaveBeenCalled(); h.controller.stop();
    });

});
