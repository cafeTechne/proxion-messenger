import { describe, expect, it } from 'vitest';
import { encodeSealedPlain } from './e2e-secondary.js';
import { encodeNativeReaction, decodeNativeReaction, isNativeReactionPlain } from './native-reaction-codec.js';
const ctx = { threadId: 'thread', selfWebId: 'did:key:alice', peerWebId: 'did:key:bob' };
const file = actor => ({ message_id: `native:${actor}:same`, thread_id: 'thread', from_webid: actor, file: { filename: 'secret.txt' } });
const input = { messageId: 'action', token: '~abcdefghijk', emoji: '👍', sequence: 1, add: true };
describe('native reaction wire references', () => {
    it('distinguishes both authors sharing a transfer UUID without exposing a private key or filename', () => {
        for (const [actor, role] of [[ctx.selfWebId, 'actor'], [ctx.peerWebId, 'peer']]) {
            const encoded = encodeNativeReaction(file(actor), ctx, input);
            expect(decodeNativeReaction(encoded.plaintext, 'action')).toMatchObject({ ref: { id: 'same', author: role }, tok: input.token, seq: 1, op: 'add' });
            expect(encoded.plaintext).not.toContain('native:'); expect(encoded.plaintext).not.toContain('secret.txt');
        }
    });
    it('binds the action id and rejects additional wire fields and unknown versions', () => {
        const { body, plaintext } = encodeNativeReaction(file(ctx.peerWebId), ctx, input);
        expect(decodeNativeReaction(plaintext, 'other')).toBeNull();
        expect(decodeNativeReaction(encodeSealedPlain('native_reaction', { ...body, target: 'private' }), 'action')).toBeNull();
        const newer = plaintext.replace('"v":1', '"v":2');
        expect(isNativeReactionPlain(newer)).toBe(true); expect(decodeNativeReaction(newer, 'action')).toBeNull();
    });
    it.each([{ token: 'bad' }, { sequence: 0 }, { sequence: 2147483648 }, { emoji: '' }, { emoji: 'x'.repeat(65) }, { add: undefined }])('rejects invalid action fields %j', patch => {
        expect(() => encodeNativeReaction(file(ctx.peerWebId), ctx, { ...input, ...patch })).toThrow();
    });
    it('rejects files outside the authenticated conversation before encoding', () => {
        expect(() => encodeNativeReaction({ ...file(ctx.peerWebId), thread_id: 'other' }, ctx, input)).toThrow();
    });
});
