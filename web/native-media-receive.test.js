import { describe, it, expect, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { createDmReceiptStore } from './dm-receipts.js';
import { encodeSealedPlain, KIND_FILE, prepareSecondaryFile } from './e2e-secondary.js';
import { createNativeMediaReceiver, nativeMediaEnvelope } from './native-media-receive.js';

async function harness() {
    const prepared = await prepareSecondaryFile(new TextEncoder().encode('private attachment'),
        { name: 'private.txt', mime: 'text/plain' });
    const event = { type: 'message', native_media_v: 1, from_webid: 'did:key:alice',
        message_id: 'file-1', thread_id: 'thread', e2e: true, e2e_v: 1,
        content: 'opaque-header', nonce: 'nonce', ratchet_pub: 'pub', pn: 0, msg_num: 0,
        file: { filename: 'file', mime_type: 'application/octet-stream', size: prepared.ct.length,
            data_b64: btoa(String.fromCharCode(...prepared.ct)) },
        native_commit: { v: 1, queue_id: 1, envelope_digest: 'a'.repeat(64) } };
    let scope = 'bob/device', generation = 0;
    const journal = new Map(), order = [], commits = [], messages = [];
    const receipts = createDmReceiptStore(new IDBFactory());
    const e2e = {
        ratchetDecrypt: vi.fn(async (peerId, ct, nonce, n, pub, pn, opts) => {
            if (!opts.current()) throw Error('stale');
            const row = { peerId, receiveId: opts.receiveId,
                content: encodeSealedPlain(KIND_FILE, prepared.body), data: opts.receiveData };
            journal.set(opts.receiveId, row); order.push('journal'); return row.content;
        }),
        pendingDmReceives: vi.fn(async () => [...journal.values()]),
        finishDmReceive: vi.fn(async (peer, id, current) => {
            if (!current()) return false;
            journal.delete(id); order.push('finish'); return true;
        }),
    };
    const persistMessage = vi.fn(async () => { order.push('history'); return true; });
    const sendCommit = vi.fn(async command => { order.push(command.cmd); commits.push(command); return true; });
    const options = { e2e, receipts, persistMessage, sendCommit, isPersistedFile: vi.fn(async () => false),
        getScope: () => scope, getGeneration: () => generation,
        toMessage: ev => ({ message_id: ev.message_id, thread_id: ev.thread_id, from_webid: ev.from_webid }),
        handleEvent: vi.fn(async message => { messages.push(message); }), requestReplay: vi.fn(), onError: vi.fn() };
    return { event, prepared, receipts, journal, e2e, order, commits, messages, options,
        engine: () => createNativeMediaReceiver(options),
        changeAccount: () => { scope = 'other/device'; generation++; } };
}

describe('native media durable receive adapter', () => {
    it('commits the header after the key journal, then history and a protected file receipt', async () => {
        const h = await harness(), engine = h.engine();
        expect(await engine.receive(h.event)).toBe(true);
        expect(h.order).toEqual(['journal', 'native_media_header_commit', 'history', 'native_media_commit', 'finish']);
        expect(atob(h.messages[0].file.data_b64)).toBe('private attachment');
        expect(h.journal.size).toBe(0);
        const pending = await h.receipts.pendingCommits('native-media:bob/device');
        expect(pending).toEqual([h.commits[1]]);
        expect(await engine.confirmCommit({ type: 'native_media_header_committed', ...pending[0] })).toBe(false);
        expect(await engine.confirmCommit({ type: 'native_media_committed', ...pending[0], envelope_digest: 'b'.repeat(64) })).toBe(false);
        expect(await engine.confirmCommit({ type: 'native_media_committed', ...pending[0] })).toBe(true);
        expect(await h.receipts.pendingCommits('native-media:bob/device')).toEqual([]);
        expect(await h.receipts.pendingCommits('bob/device')).toEqual([]);
    });
    it('retains key metadata when history fails and replays ciphertext after a reload', async () => {
        const h = await harness(), engine = h.engine();
        h.options.persistMessage.mockResolvedValueOnce(false);
        expect(await engine.receive(h.event)).toBe(false);
        expect(h.commits.map(c => c.cmd)).toEqual(['native_media_header_commit']);
        expect(h.journal.size).toBe(1);
        const row = [...h.journal.values()][0];
        expect(row.data).toEqual({ transport: 'native-media', scope: 'bob/device', commit: h.commits[0] && {
            ...h.commits[0], cmd: 'native_media_commit' } });
        expect(JSON.stringify(row.data)).not.toContain(h.event.file.data_b64);
        const reopened = h.engine(); await reopened.recover();
        expect(h.e2e.pendingDmReceives).toHaveBeenCalledWith({ transport: 'native-media' });
        expect(h.options.requestReplay).toHaveBeenCalledTimes(1);
        expect(await reopened.receive(h.event)).toBe(true);
        expect(h.messages).toHaveLength(1);
    });
    it('retains the journal and history without final ack when receipt storage fails', async () => {
        const h = await harness(); vi.spyOn(h.receipts, 'remember').mockRejectedValueOnce(Error('quota'));
        await expect(h.engine().receive(h.event)).rejects.toThrow('quota');
        expect(h.journal.size).toBe(1); expect(h.options.persistMessage).toHaveBeenCalledTimes(1);
        expect(h.commits.map(c => c.cmd)).toEqual(['native_media_header_commit']);
        expect(await h.engine().receive(h.event)).toBe(true);
    });
    it('retries the full receipt after lost acknowledgement without re-decryption or history duplication', async () => {
        const h = await harness(); await h.engine().receive(h.event);
        const decrypts = h.e2e.ratchetDecrypt.mock.calls.length;
        await h.engine().recover();
        expect(h.commits.at(-1).cmd).toBe('native_media_commit');
        await Promise.all([h.engine().receive(h.event), h.engine().receive(h.event)]);
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(decrypts);
        expect(h.messages).toHaveLength(1); expect(h.options.persistMessage).toHaveBeenCalledTimes(1);
    });
    it('does not spend a receive key when protected receipts are full', async () => {
        const h = await harness(); vi.spyOn(h.receipts, 'hasSpace').mockResolvedValue(false);
        expect(await h.engine().receive(h.event)).toBe(false);
        expect(h.e2e.ratchetDecrypt).not.toHaveBeenCalled(); expect(h.commits).toEqual([]);
    });
    it('refuses an absent account scope before touching crypto or receipts', async () => {
        const h = await harness(); h.options.getScope = () => null;
        expect(await h.engine().receive(h.event)).toBe(false);
        await h.engine().recover();
        expect(h.e2e.ratchetDecrypt).not.toHaveBeenCalled(); expect(h.e2e.pendingDmReceives).not.toHaveBeenCalled();
        expect(h.commits).toEqual([]);
    });
    it('does not acknowledge corrupt attachment bytes as complete', async () => {
        const h = await harness();
        const bytes = Uint8Array.from(atob(h.event.file.data_b64), c => c.charCodeAt(0)); bytes[0] ^= 1;
        h.event.file.data_b64 = btoa(String.fromCharCode(...bytes));
        await expect(h.engine().receive(h.event)).rejects.toThrow();
        expect(h.commits.map(c => c.cmd)).toEqual(['native_media_header_commit']);
        expect(h.journal.size).toBe(1); expect(h.options.persistMessage).not.toHaveBeenCalled();
    });
    it('binds identity to original header and ciphertext rather than route or queue annotations', async () => {
        const h = await harness(), first = await nativeMediaEnvelope(h.event);
        expect((await nativeMediaEnvelope({ ...h.event, thread_id: 'alias', timestamp: 'later',
            native_commit: { ...h.event.native_commit, queue_id: 2 } })).receiveId).toBe(first.receiveId);
        expect((await nativeMediaEnvelope({ ...h.event, content: 'other-header' })).receiveId).not.toBe(first.receiveId);
        await expect(nativeMediaEnvelope({ ...h.event, native_commit: undefined })).rejects.toThrow();
        await expect(nativeMediaEnvelope({ ...h.event, file: { ...h.event.file, size: 1 } })).rejects.toThrow();
    });
    it('stops side effects when accounts switch during history persistence', async () => {
        const h = await harness(); h.options.persistMessage.mockImplementation(async () => { h.changeAccount(); return true; });
        expect(await h.engine().receive(h.event)).toBe(false);
        expect(h.commits.map(c => c.cmd)).toEqual(['native_media_header_commit']);
        expect(h.journal.size).toBe(1); expect(h.messages).toEqual([]);
        await h.engine().recover(); expect(h.commits).toHaveLength(1);
    });
});

it('replays protected full and decline receipts in bounded rotating batches, including header proof for a lost decline ACK', async () => {
    const h = await harness(), engine = h.engine();
    for (const kind of ['full', 'decline']) {
        for (let i = 0; i < 9; i++) {
            const fingerprint = (i + (kind === 'full' ? 1 : 100)).toString(16).padStart(64, '0');
            const command = { cmd: kind === 'full' ? 'native_media_commit' : 'native_media_decline', v: 1,
                queue_id: i + 1, from_webid: 'did:key:alice', message_id: kind + '-' + i,
                envelope_digest: 'a'.repeat(64), receive_id: fingerprint };
            await h.receipts.remember((kind === 'full' ? 'native-media:' : 'native-decline:') + 'bob/device', fingerprint, null, command);
        }
    }
    for (let i = 0; i < 3; i++) {
        const count = h.commits.length; await engine.recover();
        expect(h.commits.length - count).toBeLessThanOrEqual(12);
    }
    expect(new Set(h.commits.filter(command => command.cmd === 'native_media_commit').map(command => command.message_id)).size).toBe(9);
    expect(new Set(h.commits.filter(command => command.cmd === 'native_media_decline').map(command => command.message_id)).size).toBe(9);
    for (let i = 0; i < h.commits.length; i++) {
        if (h.commits[i].cmd === 'native_media_decline') expect(h.commits[i - 1]).toEqual({ ...h.commits[i], cmd: 'native_media_header_commit' });
    }
    expect(h.e2e.ratchetDecrypt).not.toHaveBeenCalled(); expect(h.options.persistMessage).not.toHaveBeenCalled();
});

it('refuses decline after retained history succeeded but the separate full receipt write failed', async () => {
    const h = await harness(), engine = h.engine();
    const remember = h.receipts.remember;
    h.receipts.remember = vi.fn(async (...args) => args[0] === 'native-media:bob/device' ? false : remember(...args));
    expect(await engine.receive(h.event)).toBe(false);
    expect(h.options.persistMessage).toHaveBeenCalledTimes(1); expect(h.journal.size).toBe(1);
    h.options.isPersistedFile.mockResolvedValue(true);
    const { bytes } = await nativeMediaEnvelope(h.event);
    const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(value => value.toString(16).padStart(2, '0')).join('');
    expect(await engine.decline(h.event, digest)).toBe(false);
    expect(h.commits.some(command => command.cmd === 'native_media_decline')).toBe(false);
    h.receipts.remember = remember;
    expect(await engine.receive(h.event)).toBe(true);
    expect(h.commits.at(-1).cmd).toBe('native_media_commit');
});


it('reports history failure on the original receive while retaining its journal and withholding full delivery', async () => {
    const h = await harness(); h.options.onState = vi.fn(); h.options.persistMessage.mockResolvedValueOnce(false);
    const engine = h.engine(); expect(await engine.receive(h.event)).toBe(false);
    const id = [...h.journal.values()][0].receiveId;
    // Feedback identifies the exact retained header journal row shown in Pending sends.
    expect(h.options.onState).toHaveBeenCalledWith({ id, state: 'history-pending' });
    expect(h.journal.has(id)).toBe(true); expect(h.commits.some(row => row.cmd === 'native_media_commit')).toBe(false);
    expect(await engine.receive(h.event)).toBe(true);
    expect(h.options.onState.mock.calls.at(-1)[0].state).toBe('committed'); expect(h.journal.size).toBe(0);
});
