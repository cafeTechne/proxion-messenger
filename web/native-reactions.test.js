import 'fake-indexeddb/auto';
import { describe, expect, it, vi } from 'vitest';
import { createNativeReactionStore } from './native-reaction-store.js';
import { createNativeReactions } from './native-reactions.js';
import { encodeNativeReaction } from './native-reaction-codec.js';
const context = { threadId: 'thread', selfWebId: 'did:key:alice', peerWebId: 'did:key:bob' };
const original = { message_id: 'native:did:key:bob:file', thread_id: 'thread', from_webid: context.peerWebId, file: { data_b64: 'eA==' } };
function harness() {
    let scope = 'alice', generation = 0;
    const jobs = [], store = createNativeReactionStore({ getScope: () => scope, databaseName: crypto.randomUUID() });
    const references = { prepareReaction: vi.fn(async (file, _view, input) => ({ ...encodeNativeReaction(file, context, input), primaryDeviceId: 'bob-device', peerPub: 'P'.repeat(43) })) };
    const options = { store, references, getScope: () => scope, getGeneration: () => generation, isActive: () => true,
        contextForView: () => context, getMessage: vi.fn(async () => original), listJobs: async () => jobs,
        queue: vi.fn(async encoded => {
            const job = { id: encoded.body.mid, scope, nativeControl: 'reaction', record: { thread_id: 'thread', from_webid: context.selfWebId, native_reaction: encoded.body } };
            jobs.push(job); return job;
        }), transmit: vi.fn(async () => true), onApplied: vi.fn(async () => {}) };
    return { options, jobs, store, references, controller: () => createNativeReactions(options), change: () => { scope = 'bob'; generation++; } };
}
const view = { type: 'dm', id: 'thread' };
const actorContext = { ...context, actorWebId: context.selfWebId };
describe('native reaction durable source intent', () => {
    it('adds, removes and re-adds with one stable token and increasing sequences after controller reload', async () => {
        const h = harness();
        for (const add of [true, false, true]) expect(await h.controller().save(view, original.message_id, '👍', add)).toEqual({ ok: true });
        expect(h.jobs.map(job => job.record.native_reaction.seq)).toEqual([1, 2, 3]);
        expect(new Set(h.jobs.map(job => job.record.native_reaction.tok)).size).toBe(1);
        expect((await h.store.rows(actorContext))[0]).toMatchObject({ op: 'add', seq: 3 });
    });
    it('coalesces concurrent duplicate adds, and an absent remove spends no preflight or header', async () => {
        const h = harness(), c = h.controller();
        expect(await c.save(view, original.message_id, '👍', false)).toEqual({ ok: true });
        expect(await Promise.all([c.save(view, original.message_id, '👍', true), c.save(view, original.message_id, '👍', true)])).toEqual([{ ok: true }, { ok: true }]);
        expect(h.references.prepareReaction).toHaveBeenCalledTimes(1); expect(h.options.queue).toHaveBeenCalledTimes(1);
    });
    it('reconciles intent retained before a failed state write before assigning a remove sequence', async () => {
        const h = harness(), apply = h.store.apply;
        h.store.apply = vi.fn().mockResolvedValueOnce(false).mockImplementation(apply);
        expect(await h.controller().save(view, original.message_id, '👍', true)).toEqual({ ok: false });
        expect(h.options.transmit).not.toHaveBeenCalled(); expect(h.jobs).toHaveLength(1);
        expect(await h.controller().save(view, original.message_id, '👍', false)).toEqual({ ok: true });
        expect(h.jobs.map(job => job.record.native_reaction.seq)).toEqual([1, 2]);
        expect(h.jobs[0].record.native_reaction.tok).toBe(h.jobs[1].record.native_reaction.tok);
        expect((await h.store.rows(actorContext))[0].op).toBe('remove');
    });
    it('does not change state or transmit if durable intent fails', async () => {
        const h = harness(); h.options.queue.mockRejectedValueOnce(Error('full'));
        expect(await h.controller().save(view, original.message_id, '👍', true)).toEqual({ ok: false });
        expect(await h.store.rows(actorContext)).toEqual([]); expect(h.options.transmit).not.toHaveBeenCalled();
    });
    it('rejects missing/unavailable/foreign originals and account changes before preflight', async () => {
        for (const file of [null, { ...original, thread_id: 'foreign' }, { ...original, file: { unavailable: true } }]) {
            const h = harness(); h.options.getMessage.mockResolvedValueOnce(file);
            expect((await h.controller().save(view, original.message_id, '👍', true)).ok).toBe(false);
            expect(h.references.prepareReaction).not.toHaveBeenCalled();
        }
        const h = harness(); h.options.getMessage.mockImplementationOnce(async () => { h.change(); return original; });
        expect((await h.controller().save(view, original.message_id, '👍', true)).ok).toBe(false);
        expect(h.references.prepareReaction).not.toHaveBeenCalled();
    });
});
