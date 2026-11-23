import { describe, it, expect } from 'vitest';
import { encodeNativeEdit, decodeNativeEdit } from './native-edit-codec.js';
const context = { threadId: 'thread', selfWebId: 'did:key:alice', peerWebId: 'did:key:bob' };
const message = { message_id: 'native:did:key:alice:file', thread_id: 'thread', from_webid: 'did:key:alice',
    file: { filename: 'private.txt', size: 30, data_b64: 'not-part-of-the-edit' } };
const input = { actionId: 'edit-1', text: 'new caption', sequence: 1, parentHeaderDigest: 'a'.repeat(64) };
const event = { type: 'native_edit', native_edit_v: 1, message_id: input.actionId,
    file_id: 'file', native_sequence: 1, parent_header_digest: input.parentHeaderDigest };

describe('native caption action codec', () => {
    it('binds only the wire reference, original parent, action identity and normalised caption', () => {
        const encoded = encodeNativeEdit(message, context, { ...input, text: '  cafe\u0301 caption  ' });
        expect(encoded.body.text).toBe('caf\u00e9 caption');
        expect(encoded.body.ref).toEqual({ v: 1, kind: 'native_file', id: 'file', author: 'actor' });
        expect(decodeNativeEdit(encoded.plaintext, event)).toEqual(expect.objectContaining(encoded.body));
        expect(encoded.plaintext).not.toContain('private.txt'); expect(encoded.plaintext).not.toContain('not-part-of-the-edit');
        expect(message.file.data_b64).toBe('not-part-of-the-edit');
    });
    it('refuses foreign author/thread and stale sequence before transport can encrypt', () => {
        for (const changed of [{ ...message, from_webid: context.peerWebId },
            { ...message, thread_id: 'foreign' }, { ...message, file: undefined }]) {
            expect(() => encodeNativeEdit(changed, context, input)).toThrow('native-edit-reference');
        }
        expect(() => encodeNativeEdit({ ...message, native_edit_seq: 1 }, context, input)).toThrow('native-edit-sequence');
        expect(() => encodeNativeEdit(message, context, { ...input, sequence: true })).toThrow('native-edit-sequence');
        expect(() => encodeNativeEdit(message, context, { ...input, actionId: 'file' })).toThrow('native-edit-payload');
    });
    it('enforces bytes including JSON expansion and rejects malformed or empty payloads', () => {
        for (const text of ['', '  ', '\u0000pxs1', '\u{1F600}'.repeat(3000), '\\'.repeat(8192)]) {
            expect(() => encodeNativeEdit(message, context, { ...input, text })).toThrow();
        }
        expect(() => encodeNativeEdit(message, context, { ...input, parentHeaderDigest: 'wrong' })).toThrow('native-edit-payload');
        expect(() => encodeNativeEdit(message, context, { ...input, actionId: '../edit' })).toThrow('native-edit-payload');
        expect(decodeNativeEdit('plain caption', event)).toBeNull();
    });
    it('rejects changed event identity, parent, sequence, kind or protocol version', () => {
        const { plaintext } = encodeNativeEdit(message, context, input);
        for (const patch of [{ message_id: 'other' }, { file_id: 'other' }, { native_sequence: 2 },
            { parent_header_digest: 'b'.repeat(64) }, { native_edit_v: true }, { type: 'message_edited' }]) {
            expect(decodeNativeEdit(plaintext, { ...event, ...patch })).toBeNull();
        }
        expect(decodeNativeEdit(plaintext.replace('"k":"native_edit"', '"k":"edit"'), event)).toBeNull();
        expect(decodeNativeEdit(plaintext.replace('"v":1', '"v":2'), event)).toBeNull();
        expect(decodeNativeEdit(plaintext.replace('"seq":1', '"seq":true'), event)).toBeNull();
    });
});
