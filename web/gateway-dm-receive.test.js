import { describe, it, expect, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { createDmReceiptStore } from './dm-receipts.js';
import { createGatewayDmReceiver, gatewayDmEnvelope, gatewayReceiveCommit, isGatewayDmReceive } from './gateway-dm-receive.js';

const event = { type: 'message', from_webid: 'alice', message_id: 'm1', thread_id: 'thread',
    content: 'cipher', nonce: 'nonce', ratchet_pub: 'ratchet', msg_num: 0, e2e: true };
function harness() {
    let scope = 'bob/device', active = true;
    const journal = new Map(), history = new Map(), delivered = [];
    const receipts = createDmReceiptStore(new IDBFactory());
    const e2e = {
        ratchetDecrypt: vi.fn(async (peerId, content, nonce, num, pub, pn, opts) => {
            if (!opts.current()) throw Error('stale receive');
            if (!journal.has(opts.receiveId)) journal.set(opts.receiveId, {
                peerId, receiveId: opts.receiveId, content: 'plain:' + content, data: opts.receiveData,
            });
            return journal.get(opts.receiveId).content;
        }),
        finishDmReceive: vi.fn(async (peerId, id, current) => {
            if (!current()) return false;
            journal.delete(id); return true;
        }),
        pendingDmReceives: vi.fn(async () => [...journal.values()]),
    };
    const persistMessage = vi.fn(async message => { history.set(message.message_id, message); return true; });
    const options = { e2e, receipts, persistMessage, handleEvent: vi.fn(message => delivered.push(message)),
        getScope: () => scope, isActive: () => active, toMessage: raw => ({ type: 'message', message_id: raw.message_id, thread_id: 'thread' }),
        onError: vi.fn(), isSealedPlain: text => text.startsWith('plain:sealed'),
    };
    return { e2e, receipts, journal, history, delivered, persistMessage, options,
        engine: () => createGatewayDmReceiver(options), scope: value => { scope = value; }, active: value => { active = value; } };
}

describe('gateway receive eligibility and exact replay identity', () => {
    it('accepts incoming text and addressed fanout, excluding echoes and files', () => {
        expect(isGatewayDmReceive(event, 'bob', 'device')).toBeTruthy();
        expect(isGatewayDmReceive(event, 'alice', 'device')).toBeFalsy();
        expect(isGatewayDmReceive({ ...event, file: {} }, 'bob', 'device')).toBeFalsy();
        expect(isGatewayDmReceive({ ...event, room_id: 'room' }, 'bob', 'device')).toBeFalsy();
        const fanout = { type: 'dm_fanout', from_webid: 'bob', message_id: 'm', to_device_id: 'device', payload: { e2e: true, nonce: 'n' } };
        expect(isGatewayDmReceive(fanout, 'bob', 'device')).toBeTruthy();
        expect(isGatewayDmReceive(fanout, 'bob', 'sibling')).toBeFalsy();
    });
    it('ignores routing/clock annotations and binds changed ciphertext and device payload', () => {
        expect(gatewayDmEnvelope({ ...event, timestamp: 'later', thread_id: 'alias' })).toEqual(gatewayDmEnvelope(event));
        expect(gatewayDmEnvelope({ ...event, content: 'different' })).not.toEqual(gatewayDmEnvelope(event));
    });
});

describe('gateway DM receive recovery', () => {
    it('serializes duplicate socket events and suppresses exact replay after reader recreation', async () => {
        const h = harness(), engine = h.engine();
        await Promise.all([engine.receive(event), engine.receive(event)]);
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(1);
        expect(h.delivered).toHaveLength(1); expect(h.history.size).toBe(1); expect(h.journal.size).toBe(0);
        await h.engine().receive({ ...event, timestamp: 'later' });
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(1);
        expect(h.delivered).toHaveLength(1);
    });
    it('keeps recovery while history fails, without duplicate live delivery', async () => {
        const h = harness(), engine = h.engine();
        h.persistMessage.mockResolvedValueOnce(false).mockResolvedValueOnce(false);
        expect(await engine.receive(event)).toBe(false);
        expect(await engine.receive(event)).toBe(false);
        expect(h.delivered).toHaveLength(1); expect(h.journal.size).toBe(1);
        await engine.recover();
        expect(h.history.get('m1').content).toBe('plain:cipher'); expect(h.journal.size).toBe(0);
        expect(h.delivered).toHaveLength(1);
    });
    it('resumes after receipt persistence fails and the reader reloads', async () => {
        const h = harness(), engine = h.engine();
        vi.spyOn(h.receipts, 'remember').mockRejectedValueOnce(Error('quota'));
        await expect(engine.receive(event)).rejects.toThrow('quota');
        expect(h.history.size).toBe(1); expect(h.journal.size).toBe(1);
        await h.engine().recover();
        expect(h.history.size).toBe(1); expect(h.journal.size).toBe(0);
    });
    it('does not let invalid ciphertext occupy the live storage retry queue', async () => {
        const h = harness(), engine = h.engine();
        h.e2e.ratchetDecrypt.mockRejectedValue(Object.assign(Error('bad ciphertext'), { name: 'E2EDecryptError' }));
        await expect(engine.receive(event)).rejects.toThrow('bad ciphertext');
        await engine.recover();
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(1);
    });

    it('retries an event in live memory if storage initially fails before decryption', async () => {
        const h = harness(), engine = h.engine();
        vi.spyOn(h.receipts, 'has').mockRejectedValueOnce(Error('offline'));
        await expect(engine.receive(event)).rejects.toThrow('offline');
        expect(h.e2e.ratchetDecrypt).not.toHaveBeenCalled();
        await engine.recover();
        expect(h.history.size).toBe(1); expect(h.journal.size).toBe(0);
    });
    it('finishes a committed receipt without replaying an interrupted cleanup', async () => {
        const h = harness(), engine = h.engine();
        h.e2e.finishDmReceive.mockRejectedValueOnce(Error('cleanup interrupted'));
        await expect(engine.receive(event)).rejects.toThrow('cleanup interrupted');
        expect(h.journal.size).toBe(1);
        await h.engine().recover();
        expect(h.delivered).toHaveLength(1); expect(h.journal.size).toBe(0);
    });
    it('does not confuse changed ciphertext with a receipt for the same message id', async () => {
        const h = harness(), engine = h.engine();
        await engine.receive(event);
        await engine.receive({ ...event, content: 'different' });
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(2);
    });
    it('leaves another account or device recovery untouched', async () => {
        const h = harness(); h.persistMessage.mockResolvedValue(false);
        await h.engine().receive(event);
        h.scope('another-account/device');
        await h.engine().recover();
        expect(h.delivered).toHaveLength(1); expect(h.journal.size).toBe(1);
    });
    it('does not persist or render a receive after account change during decrypt', async () => {
        const h = harness();
        const original = h.e2e.ratchetDecrypt.getMockImplementation();
        h.e2e.ratchetDecrypt.mockImplementation(async (...args) => {
            const text = await original(...args); h.scope('another-account/device'); return text;
        });
        expect(await h.engine().receive(event)).toBe(false);
        expect(h.delivered).toHaveLength(0); expect(h.persistMessage).not.toHaveBeenCalled();
        expect(h.journal.size).toBe(1);
    });
    it('stops recovering when this tab steps down', async () => {
        const h = harness(), engine = h.engine();
        h.active(false); await engine.receive(event); await engine.recover();
        expect(h.e2e.ratchetDecrypt).not.toHaveBeenCalled();
        expect(h.e2e.pendingDmReceives).not.toHaveBeenCalled();
    });
    it('waits for usable routing metadata before spending the key', async () => {
        const h = harness();
        const message = { type: 'message', message_id: 'm1', thread_id: null };
        h.options.toMessage = () => ({ ...message });
        const engine = h.engine();
        expect(await engine.receive(event)).toBe(false);
        expect(h.e2e.ratchetDecrypt).not.toHaveBeenCalled();
        message.thread_id = 'thread';
        await engine.recover();
        expect(h.history.get('m1').content).toBe('plain:cipher');
    });

    it('applies receipt backpressure before consuming another key', async () => {
        const h = harness(); vi.spyOn(h.receipts, 'hasSpace').mockResolvedValue(false);
        expect(await h.engine().receive(event)).toBe(false);
        expect(h.e2e.ratchetDecrypt).not.toHaveBeenCalled();
    });
    it('discards secondary sealed plaintext without leaving a stuck recovery entry', async () => {
        const h = harness();
        expect(await h.engine().receive({ ...event, content: 'sealed body' })).toBe(true);
        expect(h.delivered).toHaveLength(0); expect(h.history.size).toBe(0); expect(h.journal.size).toBe(0);
    });
});

it('does not display or finalize a delayed decrypt after same-account reauthentication', async () => {
    const h = harness(); let generation = 1, release, started;
    h.options.getGeneration = () => generation;
    const waiting = new Promise(resolve => { release = resolve; });
    const entered = new Promise(resolve => { started = resolve; });
    const decrypt = h.e2e.ratchetDecrypt.getMockImplementation();
    h.e2e.ratchetDecrypt.mockImplementationOnce(async (...args) => {
        const plain = await decrypt(...args); started(); await waiting; return plain;
    });
    const engine = h.engine(), pending = engine.receive(event);
    await entered; generation++; release();
    expect(await pending).toBe(false);
    expect(h.delivered).toEqual([]); expect(h.history.size).toBe(0); expect(h.journal.size).toBe(1);
    await engine.recover(); expect(h.delivered).toHaveLength(1); expect(h.journal.size).toBe(0);
});


describe('durable gateway commit acknowledgements', () => {
    const fanout = { type: 'dm_fanout', from_webid: 'alice', message_id: 'm1', to_device_id: 'device',
        payload: { e2e: true, content: 'cipher', nonce: 'n' },
        receive_commit: { v: 1, queue_id: 7, envelope_digest: 'a'.repeat(64) } };
    it('sends no ack while history fails, then reacks a duplicate without decrypting', async () => {
        const h = harness(), sendCommit = vi.fn(async command => {
            await h.engine().confirmCommit({ ...command, type: 'dm_receive_committed' }); return true;
        });
        h.options.sendCommit = sendCommit;
        h.persistMessage.mockResolvedValueOnce(false);
        const reader = h.engine();
        expect(await reader.receive(fanout)).toBe(false); expect(sendCommit).not.toHaveBeenCalled();
        await reader.recover(); expect(sendCommit).toHaveBeenCalledWith(expect.objectContaining(gatewayReceiveCommit(fanout)));
        expect(h.journal.size).toBe(0);
        await h.engine().receive(fanout);
        expect(sendCommit).toHaveBeenCalledTimes(2);
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(1);
    });
    it('retains the recovery journal if the commit write is interrupted', async () => {
        const h = harness(); h.options.sendCommit = vi.fn().mockReturnValueOnce(false).mockReturnValue(true);
        expect(await h.engine().receive(fanout)).toBe(false);
        expect(h.journal.size).toBe(1);
        await h.engine().recover(); expect(h.journal.size).toBe(0);
        expect(h.options.sendCommit).toHaveBeenCalledTimes(3);
    });
    it('commits consumption evidence with history disabled while the receive recovery copy still exists', async () => {
        const h = harness(); h.options.persistMessage = vi.fn(async () => true); // explicit no-history policy
        h.options.sendCommit = vi.fn(async command => {
            expect(h.journal.size).toBe(1);
            expect(await h.receipts.has('bob/device', command.receive_id)).toBe(true);
            return true;
        });
        expect(await h.engine().receive(fanout)).toBe(true);
        expect(h.history.size).toBe(0); expect(h.journal.size).toBe(0);
        expect(await h.receipts.hasSpace('bob/device')).toBe(true); // default cap has room, receipt remains protected
        expect(await h.receipts.pendingCommits('bob/device')).toHaveLength(1);
    });
    it('protects an unconfirmed receipt from eviction until the exact server reply', async () => {
        const h = harness(); h.options.receipts = createDmReceiptStore(new IDBFactory(), { max: 1 });
        h.options.sendCommit = vi.fn(() => true);
        const reader = h.engine(); expect(await reader.receive(fanout)).toBe(true);
        const command = h.options.sendCommit.mock.calls[0][0];
        expect(await h.options.receipts.hasSpace('bob/device')).toBe(false);
        expect(await reader.confirmCommit({ ...command, envelope_digest: 'b'.repeat(64) })).toBe(false);
        expect(await h.options.receipts.hasSpace('bob/device')).toBe(false);
        expect(await h.engine().confirmCommit({ ...command, type: 'dm_receive_committed' })).toBe(true);
        expect(await h.options.receipts.hasSpace('bob/device')).toBe(true);
    });
    it('does not ack an old account after asynchronous persistence', async () => {
        const h = harness(); h.options.sendCommit = vi.fn();
        h.persistMessage.mockImplementation(async () => { h.scope('replacement'); return true; });
        expect(await h.engine().receive(fanout)).toBe(false);
        expect(h.options.sendCommit).not.toHaveBeenCalled(); expect(h.journal.size).toBe(1);
    });
    it('rejects unversioned, malformed and non-fanout tokens', () => {
        expect(gatewayReceiveCommit(event)).toBeNull();
        expect(gatewayReceiveCommit({ ...fanout, receive_commit: { ...fanout.receive_commit, queue_id: -1 } })).toBeNull();
        expect(gatewayReceiveCommit({ ...fanout, receive_commit: { ...fanout.receive_commit, v: 2 } })).toBeNull();
    });
});
