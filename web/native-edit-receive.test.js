import 'fake-indexeddb/auto';
import { beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('./auth.js', () => ({ accountDbName: name => `${name}:native-edit-receiver` }));
import { createNativeEditReceiver, nativeEditReceiveIdentity } from './native-edit-receive.js';
import { encodeNativeEdit } from './native-edit-codec.js';
import { createDmReceiptStore } from './dm-receipts.js';
import { dmHistoryApplyNativeEdit, dmHistoryClearAll, dmHistoryGet, dmHistorySaveFile, dmHistorySetEnabled } from './dmhistory.js';
const source = { threadId: 'thread', selfWebId: 'did:key:alice', peerWebId: 'did:key:bob' };
const peer = { threadId: 'thread', selfWebId: 'did:key:bob', peerWebId: 'did:key:alice', actorWebId: 'did:key:alice' };
const message = { message_id: 'native:did:key:alice:file', thread_id: 'thread', from_webid: 'did:key:alice', content: 'original',
    file: { filename: 'file.txt', mime_type: 'text/plain', size: 5 } };
const plaintext = encodeNativeEdit(message, source, { actionId: 'edit-1', text: 'caption', sequence: 1, parentHeaderDigest: 'a'.repeat(64) }).plaintext;
const event = { type: 'native_edit', native_edit_v: 1, from_webid: 'did:key:alice', thread_id: 'gateway-alias', message_id: 'edit-1',
    file_id: 'file', native_sequence: 1, parent_header_digest: 'a'.repeat(64), recipient_pub: 'A'.repeat(43),
    e2e: true, e2e_v: 1, content: 'ciphertext', nonce: 'A'.repeat(16), msg_num: 0, pn: 0, ratchet_pub: 'A'.repeat(43),
    native_commit: { v: 1, queue_id: 7, envelope_digest: 'b'.repeat(64) } };
function harness() {
    let scope = crypto.randomUUID(), generation = 0, ownPub = event.recipient_pub;
    const receipts = createDmReceiptStore(), journal = new Map(), send = vi.fn(() => true), applied = vi.fn();
    const e2e = {
        ratchetDecrypt: vi.fn(async (actor, ct, nonce, num, pub, pn, opts) => {
            if (!journal.has(opts.receiveId)) journal.set(opts.receiveId, { peerId: actor, receiveId: opts.receiveId, content: plaintext, data: opts.receiveData });
            return journal.get(opts.receiveId).content;
        }),
        finishDmReceive: vi.fn(async (actor, id) => { journal.delete(id); return true; }),
        pendingDmReceives: vi.fn(async () => [...journal.values()]),
    };
    const deps = { e2e, receipts, getScope: () => scope, getGeneration: () => generation, getOwnPub: () => ownPub,
        contextForPeer: actor => actor === peer.actorWebId ? peer : null, applyEdit: vi.fn(dmHistoryApplyNativeEdit), onApplied: applied, send };
    return { deps, e2e, receipts, journal, send, applied, receiver: createNativeEditReceiver(deps),
        switchAccount: () => { scope = crypto.randomUUID(); generation++; }, changeKey: () => { ownPub = 'B'.repeat(43); },
        receiptScope: () => 'native-edit:' + scope };
}
beforeEach(async () => {
    dmHistorySetEnabled(true); await dmHistoryClearAll();
    expect(await dmHistorySaveFile(message, new Blob(['bytes']))).toBe(true);
});
describe('native caption receive commit', () => {
    it('journals a validated caption, persists history, then commits without attachment changes', async () => {
        const h = harness(); const states = [];
        h.send.mockImplementation(async command => { states.push([command.cmd, (await dmHistoryGet(message.message_id)).content]); return true; });
        expect(await h.receiver.receive(event)).toBe(true);
        expect(states).toEqual([['native_media_header_commit', 'original'], ['native_media_commit', 'caption']]);
        expect((await dmHistoryGet(message.message_id)).file.data_b64).toBe('Ynl0ZXM=');
        expect(h.journal.size).toBe(0); expect(h.applied).toHaveBeenCalledOnce();
        const opts = h.e2e.ratchetDecrypt.mock.calls[0][6]; expect(opts.allowInit).toBe(false);
        expect(opts.receiveData.transport).toBe('native-edit'); expect(JSON.stringify(opts.receiveData)).not.toContain('file.txt');
    });
    it('retries failed history from the protected journal after controller restart', async () => {
        const h = harness(); h.deps.applyEdit.mockResolvedValueOnce(false);
        expect(await h.receiver.receive(event)).toBe(false);
        expect(h.send.mock.calls.map(([cmd]) => cmd.cmd)).toEqual(['native_media_header_commit']);
        expect(h.journal.size).toBe(1); expect(h.e2e.finishDmReceive).not.toHaveBeenCalled();
        h.receiver.stop(); const restarted = createNativeEditReceiver(h.deps);
        expect(await restarted.recover()).toBe(true);
        expect((await dmHistoryGet(message.message_id)).content).toBe('caption');
        expect(h.journal.size).toBe(0); expect(h.e2e.ratchetDecrypt).toHaveBeenCalledOnce();
    });
    it('keeps committed history idempotent when writing the full receipt fails', async () => {
        const h = harness(); vi.spyOn(h.receipts, 'remember').mockResolvedValueOnce(false);
        expect(await h.receiver.receive(event)).toBe(false);
        expect(h.send.mock.calls.map(([cmd]) => cmd.cmd)).toEqual(['native_media_header_commit']);
        expect(await h.receiver.receive(event)).toBe(true);
        expect((await dmHistoryGet(message.message_id)).edits).toHaveLength(1);
    });
    it('replays a protected full receipt after a lost send/reply without spending another key', async () => {
        const h = harness(); h.send.mockImplementation(command => command.cmd !== 'native_media_commit');
        expect(await h.receiver.receive(event)).toBe(false); expect(h.journal.size).toBe(1);
        h.send.mockReturnValue(true); expect(await h.receiver.receive(event)).toBe(true);
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledOnce();
        const commit = (await h.receipts.pendingCommits(h.receiptScope()))[0];
        expect(commit.native_edit_v).toBe(1);
        expect(await h.receiver.confirm({ ...commit, type: 'native_media_committed', native_edit_v: undefined })).toBe(false);
        expect(await h.receiver.confirm({ ...commit, type: 'native_media_committed', envelope_digest: 'f'.repeat(64) })).toBe(false);
        expect(await h.receiver.confirm({ ...commit, type: 'native_media_committed' })).toBe(true);
        expect(await h.receipts.pendingCommits(h.receiptScope())).toEqual([]);
    });
    it('requires capacity before consuming a key and refuses a missing original without a full ACK', async () => {
        const h = harness(); vi.spyOn(h.receipts, 'hasSpace').mockResolvedValueOnce(false);
        expect(await h.receiver.receive(event)).toBe(false); expect(h.e2e.ratchetDecrypt).not.toHaveBeenCalled();
        await dmHistoryClearAll(); expect(await h.receiver.receive(event)).toBe(false);
        expect(h.send.mock.calls.map(([cmd]) => cmd.cmd)).toEqual(['native_media_header_commit']);
        expect(h.journal.size).toBe(1); expect(await dmHistoryGet(message.message_id)).toBeNull();
    });
    it('rejects unknown peers, wrong keys and routing/identity mutations', async () => {
        const h = harness();
        expect(await h.receiver.receive({ ...event, from_webid: 'did:key:foreign' })).toBe(false);
        expect(await h.receiver.receive({ ...event, recipient_pub: 'B'.repeat(43) })).toBe(false);
        expect(h.e2e.ratchetDecrypt).not.toHaveBeenCalled();
        for (const patch of [{ file_id: 'other' }, { native_sequence: 2 }, { parent_header_digest: 'f'.repeat(64) }]) {
            expect(await h.receiver.receive({ ...event, ...patch })).toBe(false);
        }
        expect(h.send).not.toHaveBeenCalled();
    });
    it('does not acknowledge after an account/key change during decryption', async () => {
        for (const change of ['switchAccount', 'changeKey']) {
            const h = harness(), decrypt = h.e2e.ratchetDecrypt.getMockImplementation();
            h.e2e.ratchetDecrypt.mockImplementation(async (...args) => { const text = await decrypt(...args); h[change](); return text; });
            expect(await h.receiver.receive(event)).toBe(false); expect(h.send).not.toHaveBeenCalled();
            expect(h.e2e.finishDmReceive).not.toHaveBeenCalled();
        }
    });
    it('binds action identity to the entire sealed header and immutable target, excluding queue routing', async () => {
        const id = await nativeEditReceiveIdentity(event);
        expect((await nativeEditReceiveIdentity({ ...event, thread_id: 'new-sidebar', timestamp: 'later' })).receiveId).toBe(id.receiveId);
        expect((await nativeEditReceiveIdentity({ ...event, msg_num: 1 })).receiveId).not.toBe(id.receiveId);
        const extra = await nativeEditReceiveIdentity({ ...event, native_commit: { ...event.native_commit, untrusted: 'discard' } });
        expect(extra.event.native_commit).toEqual(event.native_commit);
        for (const patch of [{ native_edit_v: true }, { native_sequence: true }, { room_id: 'room' }, { file_id: '../file' },
            { nonce: 'wrong' }, { content: 'x'.repeat(16385) }, { native_commit: { ...event.native_commit, queue_id: true } }]) {
            await expect(nativeEditReceiveIdentity({ ...event, ...patch })).rejects.toThrow('Invalid native caption envelope');
        }
    });
});
