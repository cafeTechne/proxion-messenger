import { describe, it, expect, vi } from 'vitest';
import { createDmOutbox } from './dm-outbox.js';
const clone = value => JSON.parse(JSON.stringify(value));
const input = { id: 'message-1', peer: 'bob', actor: 'alice', device: 'alice-device', pub: 'alice-pub',
    mode: 'single', entries: [{ peerId: 'bob', account: 'bob', primary: true }],
    route: { cmd: 'local_dm', target_webid: 'bob', content: 'hello' },
    record: { message_id: 'message-1', thread_id: 'thread', from_webid: 'alice', from_display_name: 'Alice',
        content: 'hello', timestamp: '2026-01-01T00:00:00.000Z' } };
function harness() {
    let scope = 'alice/device', active = true;
    const disk = new Map(), cipher = new Map(), history = new Map(), frames = [];
    const e2e = {
        listDmSendJobs: vi.fn(async () => [...disk.values()].map(clone)),
        saveDmSendJob: vi.fn(async (job, current) => { if (!current()) throw Error('stale'); disk.set(job.id, clone(job)); }),
        removeDmSendJob: vi.fn(async (id, current) => { if (!current()) return false; disk.delete(id); return true; }),
        ratchetEncrypt: vi.fn(async (peer, text, opts) => {
            const key = peer + opts.sendId;
            if (!cipher.has(key)) cipher.set(key, { ciphertext: peer + '-ct', nonce: peer + '-nonce', msgNum: 0, pn: 0, ratchetPub: peer + '-ratchet' });
            return clone(cipher.get(key));
        }),
        hasPendingDmSend: vi.fn(async (peer, id) => cipher.has(peer + id)),
        finishDmSend: vi.fn(async (peer, id, current) => { if (!current()) return false; cipher.delete(peer + id); return true; }),
    };
    const options = { e2e, getScope: () => scope, isActive: () => active,
        persistMessage: vi.fn(async record => { history.set(record.message_id, clone(record)); return true; }),
        clearDelivery: vi.fn(async () => true), onPending: vi.fn(), onConfirmed: vi.fn(), onError: vi.fn(),
        send: vi.fn(async frame => { frames.push(clone(frame)); }),
    };
    return { e2e, options, disk, cipher, history, frames, engine: () => createDmOutbox(options),
        scope: value => { scope = value; }, active: value => { active = value; } };
}

describe('durable encrypted text outbox', () => {
    it('commits intent before encryption and prepared frames before transmission', async () => {
        const h = harness(), engine = h.engine();
        await engine.queue(input);
        expect(h.e2e.ratchetEncrypt).not.toHaveBeenCalled(); expect(h.frames).toHaveLength(0);
        h.options.send.mockImplementation(async frame => {
            expect(h.disk.get(input.id).frames[0]).toEqual(frame);
            expect(h.history.get(input.id).content).toBe('hello');
            h.frames.push(clone(frame));
        });
        await engine.send(input.id);
        expect(h.frames).toHaveLength(1); expect(h.disk.size).toBe(1); // sending is not confirmation
    });
    it('does not consume keys if intent storage fails', async () => {
        const h = harness(); h.e2e.saveDmSendJob.mockRejectedValueOnce(Error('quota'));
        await expect(h.engine().queue(input)).rejects.toThrow('quota');
        expect(h.e2e.ratchetEncrypt).not.toHaveBeenCalled(); expect(h.frames).toHaveLength(0);
    });
    it('reuses exact prepared ciphertext after reload and failed transmission', async () => {
        const h = harness(), engine = h.engine();
        await engine.queue(input);
        h.options.send.mockRejectedValueOnce(Error('socket closed'));
        expect(await engine.send(input.id)).toBe(false);
        const prepared = clone(h.disk.get(input.id).frames);
        await h.engine().restore({ force: true });
        expect(h.frames).toEqual(prepared); expect(h.e2e.ratchetEncrypt).toHaveBeenCalledTimes(1);
        expect(h.frames[0].timestamp).toBe(input.record.timestamp);
        expect(h.frames[0].from_display_name).toBe('Alice');
    });
    it('does not send after history persistence fails, and retries without encryption', async () => {
        const h = harness(), engine = h.engine();
        await engine.queue(input); h.options.persistMessage.mockResolvedValueOnce(false);
        expect(await engine.send(input.id)).toBe(false); expect(h.frames).toHaveLength(0);
        await h.engine().restore({ force: true });
        expect(h.frames).toHaveLength(1); expect(h.e2e.ratchetEncrypt).toHaveBeenCalledTimes(1);
    });
    it('keeps a failed prepared-record commit recoverable from per-peer pending sends', async () => {
        const h = harness(), engine = h.engine(); await engine.queue(input);
        h.e2e.saveDmSendJob.mockRejectedValueOnce(Error('prepared write failed'));
        expect(await engine.send(input.id)).toBe(false); expect(h.frames).toHaveLength(0); expect(h.cipher.size).toBe(1);
        await h.engine().restore({ force: true });
        expect(h.frames[0].msg_num).toBe(0); expect(h.cipher.size).toBe(1);
    });
    it('ignores own-device fanout acknowledgements for a single recipient send', async () => {
        const h = harness(), engine = h.engine(); await engine.queue(input); await engine.send(input.id);
        expect(await engine.confirm(input.id, { kind: 'fanout', delivered: [{ to_webid: 'alice', to_device_id: 'other' }] })).toBe(false);
        expect(h.disk.size).toBe(1);
        expect(await engine.confirm(input.id, { kind: 'echo', from: 'mallory' })).toBe(false);
        expect(await engine.confirm(input.id, { kind: 'echo', from: 'alice' })).toBe(true);
        expect(h.disk.size).toBe(0); expect(h.cipher.size).toBe(0);
    });
    it('requires all intended recipient devices in a gateway fanout acknowledgement', async () => {
        const h = harness(), engine = h.engine();
        const job = { ...clone(input), mode: 'fanout', entries: [
            { peerId: 'bob#b1', account: 'bob', device: 'b1', pub: 'pub1' },
            { peerId: 'bob#b2', account: 'bob', device: 'b2', pub: 'pub2' },
            { peerId: 'alice#a2', account: 'alice', device: 'a2', pub: 'pub3' },
        ] };
        await engine.queue(job); await engine.send(job.id);
        expect(h.frames[0].fanout).toHaveLength(3);
        expect(await engine.confirm(job.id, { kind: 'fanout', delivered: [{ to_webid: 'bob', to_device_id: 'b1' }] })).toBe(false);
        expect(await engine.confirm(job.id, { kind: 'fanout', delivered: [
            { to_webid: 'bob', to_device_id: 'b1' }, { to_webid: 'bob', to_device_id: 'b2' },
        ] })).toBe(true);
        expect(h.disk.size).toBe(0);
    });
    it('does not let a stale restore snapshot undo a committed confirmation', async () => {
        const h = harness(), engine = h.engine(); await engine.queue(input); await engine.send(input.id);
        const stale = clone(h.disk.get(input.id));
        let resolve;
        h.e2e.listDmSendJobs.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
        const restoring = engine.restore({ force: true });
        h.options.clearDelivery.mockResolvedValueOnce(false);
        expect(await engine.confirm(input.id, { kind: 'echo', from: 'alice' })).toBe(true);
        expect(h.disk.get(input.id).confirmed).toBe(true);
        resolve([stale]); await restoring;
        expect(h.frames).toHaveLength(1);
        expect(h.disk.size).toBe(0);
    });

    it('does not retransmit a confirmed job if cleanup was interrupted', async () => {
        const h = harness(), engine = h.engine(); await engine.queue(input); await engine.send(input.id);
        h.e2e.removeDmSendJob.mockResolvedValueOnce(false);
        expect(await engine.confirm(input.id, { kind: 'pod', from: 'alice' })).toBe(true);
        expect(h.disk.get(input.id).confirmed).toBe(true);
        h.frames.length = 0;
        await h.engine().restore({ force: true });
        expect(h.frames).toHaveLength(0); expect(h.disk.size).toBe(0);
    });
    it('keeps confirmed cleanup retryable when the history flag cannot be cleared', async () => {
        const h = harness(), engine = h.engine(); await engine.queue(input); await engine.send(input.id);
        h.options.clearDelivery.mockResolvedValueOnce(false);
        await engine.confirm(input.id, { kind: 'echo', from: 'alice' });
        expect(h.disk.get(input.id).confirmed).toBe(true); expect(h.cipher.size).toBe(1);
        await h.engine().restore(); expect(h.disk.size).toBe(0); expect(h.frames).toHaveLength(1);
    });
    it('ignores jobs belonging to another account/device and stops stale preparation', async () => {
        const h = harness(), engine = h.engine(); await engine.queue(input);
        h.scope('another/account'); await h.engine().restore({ force: true }); expect(h.frames).toHaveLength(0);
        h.scope('alice/device');
        const original = h.e2e.ratchetEncrypt.getMockImplementation();
        h.e2e.ratchetEncrypt.mockImplementation(async (...args) => { const enc = await original(...args); h.scope('another/account'); return enc; });
        expect(await engine.send(input.id)).toBe(false); expect(h.frames).toHaveLength(0);
    });
    it('does not consume pending jobs when this tab is inactive', async () => {
        const h = harness(); h.active(false);
        await expect(h.engine().queue(input)).rejects.toThrow('context changed');
        expect(h.e2e.ratchetEncrypt).not.toHaveBeenCalled();
    });
    it('waits for its route to be ready without forgetting prepared frames', async () => {
        const h = harness(); let ready = false; h.options.canSend = () => ready;
        const engine = h.engine(); await engine.queue(input); expect(await engine.send(input.id)).toBe(false);
        ready = true; await engine.send(input.id);
        expect(h.frames).toHaveLength(1); expect(h.e2e.ratchetEncrypt).toHaveBeenCalledTimes(1);
    });
});

it('abandons an old session generation even when the account/device scope is unchanged', async () => {
    const h = harness(); let generation = 1, release, started;
    h.options.getGeneration = () => generation;
    const waiting = new Promise(resolve => { release = resolve; });
    const entered = new Promise(resolve => { started = resolve; });
    const encrypt = h.e2e.ratchetEncrypt.getMockImplementation();
    h.e2e.ratchetEncrypt.mockImplementationOnce(async (...args) => { started(); await waiting; return encrypt(...args); });
    const engine = h.engine(); await engine.queue(input);
    const pending = engine.send(input.id); await entered; generation++; release();
    expect(await pending).toBe(false); expect(h.frames).toEqual([]);
    expect(h.disk.get(input.id).frames).toBeUndefined();
    expect(await engine.send(input.id)).toBe(true);
    expect(h.frames).toHaveLength(1);
});

describe('safe pending-send controls', () => {
    it('keeps offline intents unencrypted and lets the user stop them', async () => {
        const h = harness(); h.options.canSend = () => false; h.options.onCancelled = vi.fn();
        const engine = h.engine(); await engine.queue(input);
        expect(await engine.send(input.id)).toBe(false);
        expect(h.e2e.ratchetEncrypt).not.toHaveBeenCalled();
        expect(await engine.inspect()).toMatchObject([{ id: input.id, state: 'waiting', canCancel: true }]);
        expect(await engine.cancel(input.id)).toBe(true); expect(h.options.onCancelled).toHaveBeenCalledWith(input.id);
        expect(h.disk.size).toBe(0); expect(await engine.inspect()).toEqual([]);
    });
    it('refuses cancellation after interrupted preparation spent a key, without frames on disk', async () => {
        const h = harness(), engine = h.engine(); await engine.queue(input);
        h.e2e.saveDmSendJob.mockRejectedValueOnce(Error('prepare commit failed'));
        expect(await engine.send(input.id)).toBe(false);
        expect(h.disk.get(input.id).frames).toBeUndefined();
        expect((await engine.inspect())[0].canCancel).toBe(false);
        expect(await engine.cancel(input.id)).toBe(false); expect(h.disk.size).toBe(1);
        await engine.send(input.id); expect(h.frames[0].msg_num).toBe(0);
    });
    it('refuses to retarget a frozen device plan and retains prepared sends for exact retry', async () => {
        const h = harness(); let plan = 'ready'; h.options.planStatus = () => plan;
        const engine = h.engine(); await engine.queue(input); await engine.send(input.id);
        const frame = clone(h.frames[0]); plan = 'changed';
        expect((await engine.inspect())[0]).toMatchObject({ state: 'attention', canCancel: false });
        expect(await engine.cancel(input.id)).toBe(false); expect(await engine.send(input.id)).toBe(false);
        expect(h.frames).toHaveLength(1); plan = 'ready'; await engine.send(input.id);
        expect(h.frames[1]).toEqual(frame); expect(h.e2e.ratchetEncrypt).toHaveBeenCalledTimes(1);
    });
    it('serializes concurrent intent creation against the queue capacity', async () => {
        const h = harness(), engine = h.engine();
        for (let i = 0; i < 127; i++) h.disk.set('old' + i, { scope: 'alice/device', id: 'old' + i });
        const second = clone(input); second.id = 'message-2'; second.record.message_id = second.id;
        const results = await Promise.allSettled([engine.queue(input), engine.queue(second)]);
        expect(results.map(row => row.status)).toEqual(['fulfilled', 'rejected']); expect(h.disk.size).toBe(128);
    });
    it('waits for active preparation before deciding whether cancellation is safe', async () => {
        const h = harness(), engine = h.engine(); await engine.queue(input);
        let release, started;
        const waiting = new Promise(resolve => { release = resolve; });
        const entered = new Promise(resolve => { started = resolve; });
        const encrypt = h.e2e.ratchetEncrypt.getMockImplementation();
        h.e2e.ratchetEncrypt.mockImplementationOnce(async (...args) => { started(); await waiting; return encrypt(...args); });
        const sending = engine.send(input.id); await entered;
        const cancelling = engine.cancel(input.id); release(); await sending;
        expect(await cancelling).toBe(false); expect(h.disk.size).toBe(1);
    });
});
