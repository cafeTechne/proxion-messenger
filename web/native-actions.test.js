import { describe, expect, it, vi } from 'vitest';
import { createNativeActions } from './native-actions.js';
import { encodeNativeAction, encodeRetainedNativeUnpin } from './native-action-codec.js';
const ctx = { threadId: 'thread', selfWebId: 'did:key:alice', peerWebId: 'did:key:bob' };
const original = { message_id: 'native:did:key:alice:file', thread_id: 'thread', from_webid: ctx.selfWebId, file: { filename: 'private.txt' } };
const view = { type: 'dm', id: 'thread' };
function harness() {
    let scope = 'alice'; const jobs = [], states = new Map();
    const apply = vi.fn(async body => {
        const row = states.get(original.message_id) || { message_id: original.message_id };
        if (body.op === 'delete') row.deletedEveryone = true;
        else row.pin = { active: body.op === 'pin', clock: body.clock, mid: body.mid };
        states.set(original.message_id, row); return true;
    });
    const options = { getScope: () => scope, getGeneration: () => 0, isActive: () => true, contextForView: () => ctx,
        getMessage: vi.fn(async () => original), listJobs: async () => jobs, readState: async () => [...states.values()], apply,
        references: { prepareAction: vi.fn(async (file, _view, input) => encodeNativeAction(file, ctx, input)) },
        queue: vi.fn(async encoded => { const job = { scope, id: encoded.body.mid, nativeControl: 'action',
            record: { thread_id: 'thread', from_webid: ctx.selfWebId, native_action: encoded.body } }; jobs.push(job); return job; }),
        transmit: vi.fn(async () => true), onApplied: vi.fn(async () => {}) };
    return { options, jobs, states, controller: () => createNativeActions(options), change: () => { scope = 'other'; } };
}
describe('native delete/pin source intent', () => {
    it('queues intent before local deletion and can retry a failed state write through durable intent', async () => {
        const h = harness(), apply = h.options.apply.getMockImplementation();
        h.options.apply.mockResolvedValueOnce(false);
        expect((await h.controller().save(view, original.message_id, 'delete')).ok).toBe(false);
        expect(h.jobs).toHaveLength(1); expect(h.options.transmit).not.toHaveBeenCalled();
        h.options.apply.mockImplementation(apply);
        expect((await h.controller().save(view, original.message_id, 'pin')).reason).toBe('deleted');
        expect(h.states.get(original.message_id).deletedEveryone).toBe(true); expect(h.jobs).toHaveLength(1);
    });
    it('coalesces duplicate pins and assigns a newer clock to unpin after reload', async () => {
        const h = harness(), c = h.controller();
        await Promise.all([c.save(view, original.message_id, 'pin'), c.save(view, original.message_id, 'pin')]);
        expect(h.jobs).toHaveLength(1);
        expect((await h.controller().save(view, original.message_id, 'unpin')).ok).toBe(true);
        expect(h.jobs.map(job => job.record.native_action.clock)).toEqual([1, 2]);
    });
    it('never mutates local state or transmits when intent storage fails', async () => {
        const h = harness(); h.options.queue.mockRejectedValueOnce(Error('full'));
        expect((await h.controller().save(view, original.message_id, 'delete')).ok).toBe(false);
        expect(h.options.apply).not.toHaveBeenCalled(); expect(h.options.transmit).not.toHaveBeenCalled();
    });
    it('rejects another author deletion, missing originals and account replacement before preflight', async () => {
        const h = harness();
        h.options.getMessage.mockResolvedValueOnce({ ...original, from_webid: ctx.peerWebId, message_id: 'native:did:key:bob:file' });
        expect((await h.controller().save(view, 'native:did:key:bob:file', 'delete')).ok).toBe(false);
        h.options.getMessage.mockResolvedValueOnce(null);
        expect((await h.controller().save(view, original.message_id, 'pin')).ok).toBe(false);
        h.options.getMessage.mockImplementationOnce(async () => { h.change(); return original; });
        expect((await h.controller().save(view, original.message_id, 'pin')).ok).toBe(false);
        expect(h.options.references.prepareAction).not.toHaveBeenCalled(); expect(h.options.queue).not.toHaveBeenCalled();
    });
    it('unpins an authenticated retained reference without manufacturing a missing original', async () => {
        const h = harness(); h.options.getMessage.mockResolvedValue(null);
        h.states.set(original.message_id, { message_id: original.message_id, from_webid: ctx.selfWebId, thread_id: 'thread', pin: { active: true, clock: 3 } });
        h.options.references.prepareRetainedUnpin = vi.fn(async (ref, _view, input) => encodeRetainedNativeUnpin(ref, input));
        expect((await h.controller().save(view, original.message_id, 'unpin')).ok).toBe(true);
        expect(h.options.references.prepareAction).not.toHaveBeenCalled();
        expect(h.jobs[0].record.native_action).toMatchObject({ op: 'unpin', clock: 4, ref: { author: 'actor', id: 'file' } });
        expect((await h.controller().save(view, original.message_id, 'pin')).ok).toBe(false);
        h.states.get(original.message_id).thread_id = 'foreign';
        expect((await h.controller().save(view, original.message_id, 'unpin')).ok).toBe(false);
    });

});
