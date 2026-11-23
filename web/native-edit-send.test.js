import 'fake-indexeddb/auto';
import { beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('./auth.js', () => ({ accountDbName: name => `${name}:native-edit-sender` }));
import { createNativeEditSender } from './native-edit-send.js';
import { dmHistoryApplyNativeEdit, dmHistoryClearAll, dmHistoryGet, dmHistorySaveFile, dmHistorySetEnabled } from './dmhistory.js';
const route = { threadId: 'thread', selfWebId: 'did:key:alice', peerWebId: 'did:key:bob' };
const view = { id: 'thread', type: 'dm', peerWebid: route.peerWebId };
const message = { message_id: 'native:did:key:alice:file', thread_id: 'thread', from_webid: 'did:key:alice', content: 'original',
    file: { filename: 'file.txt', mime_type: 'text/plain', size: 5 } };
function harness() {
    let scope = crypto.randomUUID(), pub = 'A'.repeat(43), prepared = 0;
    const jobs = new Map(), pending = new Map(), held = new Set(), released = new Set();
    const e2e = {
        listDmSendJobs: vi.fn(async () => [...jobs.values()].map(job => structuredClone(job))),
        saveDmSendJob: vi.fn(async job => { jobs.set(job.id, structuredClone(job)); }),
        removeDmSendJob: vi.fn(async id => { jobs.delete(id); return true; }),
        ratchetEncrypt: vi.fn(async (peer, text, opts) => {
            if (!pending.has(opts.sendId)) {
                prepared++; held.add(opts.sendId);
                pending.set(opts.sendId, { ciphertext: 'ciphertext', nonce: 'A'.repeat(16), msgNum: prepared, pn: 0, ratchetPub: 'A'.repeat(43) });
            }
            return pending.get(opts.sendId);
        }),
        releaseDmSendHold: vi.fn(async (peer, id) => { if (!held.has(id) && !released.has(id)) return false; held.delete(id); released.add(id); return true; }),
        finishDmSend: vi.fn(async (peer, id) => { if (held.has(id)) return false; pending.delete(id); return true; }),
    };
    const deps = { e2e, getScope: () => scope, getPeerPub: () => pub,
        contextForView: v => v.id === 'thread' ? route : null, getMessage: dmHistoryGet,
        capability: vi.fn(async () => ({ supported: true, peer_pub: pub, native_sequence: 1, parent_header_digest: 'a'.repeat(64) })),
        send: vi.fn(async () => true), applyEdit: vi.fn(dmHistoryApplyNativeEdit), onApplied: vi.fn() };
    return { deps, e2e, jobs, pending, held, sender: createNativeEditSender(deps),
        changedKey: () => { pub = 'B'.repeat(43); }, switchAccount: () => { scope = crypto.randomUUID(); }, prepared: () => prepared };
}
const status = (job, patch = {}) => ({ type: 'native_media_status', v: 1, native_edit_v: 1, message_id: job.id,
    status: 'pending', header_committed: false, media_digest: job.mediaDigest, header_digest: job.headerDigest, ...patch });
beforeEach(async () => {
    dmHistorySetEnabled(true); await dmHistoryClearAll();
    expect(await dmHistorySaveFile(message, new Blob(['bytes']))).toBe(true);
});
describe('durable native caption sender', () => {
    it('validates author/thread/text before capability or encryption and rejects a negative preflight', async () => {
        const h = harness();
        for (const original of [{ ...message, thread_id: 'foreign' }, { ...message, from_webid: route.peerWebId }, { ...message, file: undefined }]) {
            await expect(h.sender.enqueue(original, view, 'caption')).rejects.toThrow('native-edit-reference');
        }
        await expect(h.sender.enqueue(message, view, 'x'.repeat(8193))).rejects.toThrow('native-edit-payload');
        expect(h.deps.capability).not.toHaveBeenCalled(); expect(h.e2e.ratchetEncrypt).not.toHaveBeenCalled();
        h.deps.capability.mockResolvedValueOnce(null);
        await expect(h.sender.enqueue(message, view, 'caption')).rejects.toThrow('Native edit peer unavailable');
        expect(h.jobs.size).toBe(0); expect(h.prepared()).toBe(0);
    });
    it('writes a small protected intent before encryption and keeps exact preparation after failed job save/reload', async () => {
        const h = harness(), job = await h.sender.enqueue(message, view, 'caption');
        expect(h.prepared()).toBe(0); expect(h.jobs.get(job.id).plaintext).not.toContain('file.txt');
        const save = h.e2e.saveDmSendJob.getMockImplementation();
        h.e2e.saveDmSendJob.mockImplementationOnce(async next => { if (next.payload) throw Error('disk full'); return save(next); });
        await expect(h.sender.retry(job.id)).rejects.toThrow('disk full');
        expect(h.prepared()).toBe(1); expect(h.deps.send).not.toHaveBeenCalled();
        expect(h.jobs.get(job.id).payload).toBeUndefined(); h.sender.stop();
        const restarted = createNativeEditSender(h.deps); expect(await restarted.recover()).toBe(true);
        expect(h.prepared()).toBe(1); expect(h.deps.send).toHaveBeenCalledOnce();
        const frame = h.deps.send.mock.calls[0][0]; expect(frame.message_id).toBe(job.id);
        expect(h.e2e.ratchetEncrypt.mock.calls[0][2]).toMatchObject({ sendId: job.sendId, holdForReceive: true, peerPub: 'A'.repeat(43) });
        expect(h.pending.size).toBe(1); expect(h.held.size).toBe(1);
    });
    it('treats custody and header ACK separately from full caption/history consumption', async () => {
        const h = harness(), queued = await h.sender.enqueue(message, view, 'caption'); await h.sender.retry(queued.id);
        const job = h.jobs.get(queued.id);
        expect(await h.sender.status(status(job))).toBe(false); expect(h.held.size).toBe(1);
        expect(await h.sender.status(status(job, { header_committed: true }))).toBe(true);
        expect(h.held.size).toBe(0); expect(h.e2e.finishDmSend).not.toHaveBeenCalled();
        expect((await dmHistoryGet(message.message_id)).content).toBe('original');
        expect(await h.sender.status(status(job, { status: 'committed', header_committed: true }))).toBe(true);
        expect(h.jobs.size).toBe(0); expect(h.pending.size).toBe(0);
        expect(await dmHistoryGet(message.message_id)).toMatchObject({ content: 'caption', native_edit_seq: 1, file: { data_b64: 'Ynl0ZXM=' } });
    });
    it('retains a fully received action until source history and removal succeed', async () => {
        const h = harness(), queued = await h.sender.enqueue(message, view, 'caption'); await h.sender.retry(queued.id);
        const job = h.jobs.get(queued.id); h.deps.applyEdit.mockResolvedValueOnce(false);
        const full = status(job, { status: 'committed', header_committed: true });
        expect(await h.sender.status(full)).toBe(false); expect(h.jobs.get(job.id).committed).toBe(true);
        expect(h.pending.size).toBe(1); h.e2e.removeDmSendJob.mockResolvedValueOnce(false);
        expect(await h.sender.retry(job.id)).toBe(false); expect(h.jobs.get(job.id).historySaved).toBe(true);
        expect(h.pending.size).toBe(0); h.sender.stop();
        expect(await createNativeEditSender(h.deps).recover()).toBe(true);
        expect(h.jobs.size).toBe(0); expect((await dmHistoryGet(message.message_id)).edits).toHaveLength(1);
    });
    it('refuses forged status, conflicting captions and changed keys without replacing a prepared action', async () => {
        const h = harness(), queued = await h.sender.enqueue(message, view, 'caption');
        expect((await h.sender.enqueue(message, view, 'caption')).id).toBe(queued.id);
        await expect(h.sender.enqueue(message, view, 'different')).rejects.toThrow('Native edit already pending');
        await h.sender.retry(queued.id); const job = h.jobs.get(queued.id);
        for (const patch of [{ native_edit_v: undefined }, { media_digest: 'wrong' }, { header_digest: 'f'.repeat(64) },
            { status: 'committed', header_committed: false }, { status: 'declined', header_committed: true }]) {
            expect(await h.sender.status(status(job, patch))).toBe(false);
        }
        h.changedKey(); h.deps.send.mockClear(); expect(await h.sender.retry(job.id)).toBe(false);
        expect(h.deps.send).not.toHaveBeenCalled(); expect(h.prepared()).toBe(1); expect(h.jobs.get(job.id)).toEqual(job);
    });
    it('releases only proven header ordering for a blocked action while retaining the exact prepared plan', async () => {
        const h = harness(), queued = await h.sender.enqueue(message, view, 'caption'); await h.sender.retry(queued.id);
        const job = h.jobs.get(queued.id);
        expect(await h.sender.status(status(job, { status: 'blocked', header_committed: true }))).toBe(false);
        expect(h.held.size).toBe(0); expect(h.pending.size).toBe(1); expect(h.jobs.get(job.id).payload).toEqual(job.payload);
        expect(h.e2e.finishDmSend).not.toHaveBeenCalled(); expect(h.e2e.removeDmSendJob).not.toHaveBeenCalled();
        expect((await dmHistoryGet(message.message_id)).content).toBe('original');
    });
    it('rechecks the retained original sequence before spending a queued action header', async () => {
        const h = harness(), job = await h.sender.enqueue(message, view, 'caption');
        h.deps.getMessage = async () => ({ ...message, native_edit_seq: 1 });
        const restarted = createNativeEditSender(h.deps);
        expect(await restarted.retry(job.id)).toBe(false);
        expect(h.e2e.ratchetEncrypt).not.toHaveBeenCalled(); expect(h.deps.send).not.toHaveBeenCalled();
        expect(h.jobs.size).toBe(1);
    });
    it('can inspect retained intents while disconnected without preparing or sending them', async () => {
        const h = harness(), job = await h.sender.enqueue(message, view, 'caption');
        h.sender.stop();
        const offline = createNativeEditSender({ ...h.deps, isActive: () => false, isInspectable: () => true });
        expect(await offline.inspect()).toEqual([expect.objectContaining({ id: job.id, canCancel: false })]);
        expect(await offline.retry(job.id)).toBe(false);
        expect(h.e2e.ratchetEncrypt).not.toHaveBeenCalled(); expect(h.deps.send).not.toHaveBeenCalled();
    });
    it('keeps blocked prepared actions and ignores stale work after account changes', async () => {
        const h = harness(), queued = await h.sender.enqueue(message, view, 'caption'); await h.sender.retry(queued.id);
        const job = h.jobs.get(queued.id);
        expect(await h.sender.status(status(job, { status: 'blocked' }))).toBe(false);
        h.deps.send.mockClear(); expect(await h.sender.retry(job.id)).toBe(false); expect(h.held.size).toBe(1);
        h.switchAccount(); expect(await h.sender.recover()).toBe(true);
        expect(h.deps.send).not.toHaveBeenCalled(); expect(h.jobs.size).toBe(1);
    });
});
