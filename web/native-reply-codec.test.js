import { describe, expect, it } from 'vitest';
import { encodeNativeReply, decodeNativeReply, resolveNativeReply } from './native-reply-codec.js';
const context = { threadId: 'thread', selfWebId: 'did:key:alice', peerWebId: 'did:key:bob' };
const file = actor => ({ message_id: `native:${actor}:same-file`, thread_id: 'thread', from_webid: actor,
    file: { filename: 'private-file.txt', data_b64: 'private-file-bytes', size: 18 } });
const originals = [file(context.selfWebId), file(context.peerWebId)];
describe('native reply codec (not yet connected)', () => {
    it('separates both authors of the same wire UUID without leaking file bytes or UI keys', () => {
        for (const [index, original] of originals.entries()) {
            const encoded = encodeNativeReply(original, context, { messageId: 'reply', text: '  caption reply  ' });
            expect(encoded.body.ref.author).toBe(index === 0 ? 'actor' : 'peer');
            expect(encoded.plaintext).not.toContain('native:'); expect(encoded.plaintext).not.toContain('private-file');
            const decoded = decodeNativeReply(encoded.plaintext, 'reply');
            expect(resolveNativeReply(decoded, { ...context, actorWebId: context.selfWebId, messages: originals }))
                .toMatchObject({ content: 'caption reply', reply_to_id: original.message_id, native_reply_unavailable: false });
        }
    });
    it('maps the authenticated actor role back to the receiver namespace', () => {
        const { plaintext } = encodeNativeReply(originals[0], context, { messageId: 'reply', text: 'reply' });
        const body = decodeNativeReply(plaintext, 'reply');
        const receiver = { ...context, selfWebId: context.peerWebId, peerWebId: context.selfWebId, actorWebId: context.selfWebId, messages: originals };
        expect(resolveNativeReply(body, receiver).reply_to_id).toBe(originals[0].message_id);
    });
    it('keeps a reference for a delayed original without creating a phantom attachment', () => {
        const { body } = encodeNativeReply(originals[1], context, { messageId: 'reply', text: 'reply' });
        const missing = resolveNativeReply(body, { ...context, actorWebId: context.selfWebId, messages: [] });
        expect(missing).toMatchObject({ content: 'reply', reply_to_id: null, native_reply_unavailable: true });
        expect(missing.file).toBeUndefined();
        expect(resolveNativeReply(body, { ...context, actorWebId: 'did:key:foreign', messages: originals })).toBeNull();
        expect(resolveNativeReply(body, { ...context, actorWebId: context.selfWebId, messages: [originals[1], { ...originals[1] }] }).reply_to_id).toBeNull();
    });
    it('rejects foreign scope and malformed payloads before any crypto call', () => {
        for (const original of [{ ...originals[0], thread_id: 'foreign' }, { ...originals[0], from_webid: 'did:key:foreign' }, { ...originals[0], file: undefined }]) {
            expect(() => encodeNativeReply(original, context, { messageId: 'reply', text: 'reply' })).toThrow();
        }
        for (const text of ['', '\u0000pxs1', 'x'.repeat(8193), '\\'.repeat(8192)]) {
            expect(() => encodeNativeReply(originals[0], context, { messageId: 'reply', text })).toThrow();
        }
    });
    it('binds the encrypted reply to its normal message id and version/kind', () => {
        const { plaintext } = encodeNativeReply(originals[0], context, { messageId: 'reply', text: 'reply' });
        expect(decodeNativeReply(plaintext, 'other')).toBeNull();
        expect(decodeNativeReply(plaintext.replace('"v":1', '"v":2'), 'reply')).toBeNull();
        expect(decodeNativeReply(plaintext.replace('"k":"native_reply"', '"k":"edit"'), 'reply')).toBeNull();
        expect(decodeNativeReply(plaintext.replace('"mid":"reply"', '"extra":1,"mid":"reply"'), 'reply')).toBeNull();
    });
});
