import { describe, it, expect, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { prepareSecondaryFile, encodeSealedPlain, KIND_FILE } from './e2e-secondary.js';
import { createDmReceiptStore, dmEnvelopeFingerprint } from './dm-receipts.js';
import { createNativeMediaReceiver } from './native-media-receive.js';
import { createNativeMediaInbox } from './native-media-inbox.js';
import { createNativeChunkReceiver } from './native-media-chunks-receive.js';
const hash = async bytes => [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(byte => byte.toString(16).padStart(2, '0')).join('');
const b64 = bytes => { const parts = []; for (let i = 0; i < bytes.length; i += 32768) parts.push(String.fromCharCode(...bytes.subarray(i, i + 32768))); return btoa(parts.join('')); };
async function harness(size = 600 * 1024) {
    const plain = new Uint8Array(size).fill(65), prepared = await prepareSecondaryFile(plain, { name: 'private.txt', mime: 'text/plain' });
    const manifest = { e2e: true, e2e_v: 1, content: 'header', nonce: 'nonce', ratchet_pub: 'pub', msg_num: 0, pn: 0,
        size: prepared.ct.length, cipher_digest: await hash(prepared.ct), chunk_count: Math.ceil(prepared.ct.length / 65536), recipient_pub: 'a'.repeat(43) };
    const event = { ...manifest, type: 'native_media_offer', native_chunks_v: 1, from_webid: 'did:key:alice', message_id: 'file',
        thread_id: 'thread', manifest_digest: await dmEnvelopeFingerprint(manifest), native_commit: { v: 1, queue_id: 1, envelope_digest: 'b'.repeat(64) } };
    let scope = 'bob', pub = 'a'.repeat(43), clock = 0;
    const db = new IDBFactory(), inbox = createNativeMediaInbox(db), journal = new Map(), commits = [], sent = [], messages = [], errors = [];
    const e2e = {
        ratchetDecrypt: vi.fn(async (peerId, ct, nonce, n, rp, pn, opts) => {
            if (!opts.current()) throw Error('stale');
            if (!journal.has(opts.receiveId)) journal.set(opts.receiveId, { peerId, receiveId: opts.receiveId,
                content: encodeSealedPlain(KIND_FILE, prepared.body), data: opts.receiveData });
            return journal.get(opts.receiveId).content;
        }),
        finishDmReceive: vi.fn(async (peer, id, current) => { if (!current()) return false; journal.delete(id); return true; }),
        pendingDmReceives: vi.fn(async () => [...journal.values()]),
    };
    const receipts = createDmReceiptStore(db);
    const receiver = createNativeMediaReceiver({ e2e, receipts, getScope: () => scope, isActive: () => true,
        toMessage: ev => ({ message_id: 'native:' + ev.from_webid + ':' + ev.message_id, thread_id: ev.thread_id }),
        persistMessage: vi.fn(), handleEvent: async msg => { messages.push(msg); }, sendCommit: async cmd => { commits.push(cmd); return true; } });
    const options = { inbox, receiver, getScope: () => scope, getOwnPub: () => pub, isActive: () => true,
        send: vi.fn(async cmd => { sent.push(cmd); return true; }), persistFile: vi.fn(async () => true),
        onError: err => errors.push(err), now: () => clock };
    const engine = () => createNativeChunkReceiver(options), client = engine();
    const part = async seq => ({ type: 'native_media_chunk', native_chunks_v: 1, from_webid: event.from_webid,
        message_id: event.message_id, manifest_digest: event.manifest_digest, seq,
        data_b64: b64(prepared.ct.subarray(seq * 65536, (seq + 1) * 65536)),
        part_digest: await hash(prepared.ct.subarray(seq * 65536, (seq + 1) * 65536)) });
    return { db, inbox, e2e, receipts, receiver, options, engine, client, event, plain, journal, sent, commits, messages, errors, part,
        scope: value => { scope = value; }, pub: value => { pub = value; }, tick: () => { clock += 16000; } };
}
async function drain(h, client = h.client) {
    let consumed = 0;
    while (consumed < h.sent.length) {
        const request = h.sent[consumed++];
        for (const seq of request.sequences) await client.part(await h.part(seq));
    }
}
describe('native chunk receive recovery', () => {
    it('journals the header before requesting parts, advances bounded windows after commits and saves before full ACK', async () => {
        const h = await harness(); await h.client.offer(h.event);
        expect(h.journal.size).toBe(1); expect(h.commits.map(cmd => cmd.cmd)).toEqual(['native_media_header_commit']);
        expect(h.sent[0].sequences).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
        await drain(h);
        expect(h.options.persistFile).toHaveBeenCalledTimes(1);
        expect(new Uint8Array(await h.options.persistFile.mock.calls[0][1].arrayBuffer())).toEqual(h.plain);
        expect(h.commits.at(-1).cmd).toBe('native_media_commit'); expect(h.journal.size).toBe(0);
        expect(await h.inbox.list('bob')).toEqual([]); expect(h.messages).toHaveLength(1);
        expect(atob(h.messages[0].file.data_b64).length).toBe(h.plain.length);
    });
    it('reopens partial storage and asks only for missing parts without another receive key', async () => {
        const h = await harness(); await h.client.offer(h.event); await h.client.part(await h.part(0));
        const reopened = h.engine(); h.sent.length = 0; await reopened.recover();
        expect(h.sent[0].sequences).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
        await drain(h, reopened);
        expect(h.messages).toHaveLength(1); expect(h.errors).toEqual([]);
        expect(new Set(h.e2e.ratchetDecrypt.mock.calls.map(call => call[6].receiveId)).size).toBe(1);
    });
    it('retains complete ciphertext and key recovery when final history fails and retries after recreation', async () => {
        const h = await harness(); h.options.persistFile.mockResolvedValueOnce(false);
        await h.client.offer(h.event); await drain(h);
        expect((await h.inbox.list('bob'))[0].received).toBe(h.event.chunk_count);
        expect(h.journal.size).toBe(1); expect(h.commits.some(cmd => cmd.cmd === 'native_media_commit')).toBe(false);
        await h.engine().recover(); expect(h.messages).toHaveLength(1); expect(await h.inbox.list('bob')).toEqual([]);
    });
    it('refuses foreign/key-changed offers and wrong or unrequested parts', async () => {
        const h = await harness(); h.pub('c'.repeat(43)); expect(await h.client.offer(h.event)).toBe(false);
        expect(h.e2e.ratchetDecrypt).not.toHaveBeenCalled(); expect(await h.inbox.list('bob')).toEqual([]);
        h.pub('a'.repeat(43)); await h.client.offer(h.event);
        expect(await h.client.part({ ...await h.part(0), from_webid: 'did:key:foreign' })).toBe(false);
        expect(await h.client.part(await h.part(9))).toBe(false);
        await expect(h.client.part({ ...await h.part(0), part_digest: 'c'.repeat(64) })).rejects.toThrow('digest');
        expect((await h.inbox.list('bob'))[0].received).toBe(0);
    });
    it('retries a timed-out window from durable progress and suppresses concurrent duplicate part work', async () => {
        const h = await harness(); await h.client.offer(h.event);
        const packet = await h.part(0); await Promise.all([h.client.part(packet), h.client.part(packet)]);
        expect((await h.inbox.list('bob'))[0].received).toBe(1);
        h.tick(); await h.client.recover(); expect(h.sent.at(-1).sequences).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    });
    it('re-acks an already-consumed file after local inbox cleanup without another fetch or decrypt', async () => {
        const h = await harness(32); await h.client.offer(h.event); await drain(h);
        const decrypts = h.e2e.ratchetDecrypt.mock.calls.length, fetches = h.sent.length;
        expect(await h.engine().offer(h.event)).toBe(true);
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(decrypts); expect(h.sent).toHaveLength(fetches);
        expect(h.messages).toHaveLength(1); expect(h.commits.at(-1).cmd).toBe('native_media_commit');
    });
});


describe('durable native recipient decline', () => {
    it('journals a distinct protected outcome before removing ciphertext and never writes file history or a full commit', async () => {
        const h = await harness(); await h.client.offer(h.event); await h.client.part(await h.part(0));
        const keys = new Set(h.e2e.ratchetDecrypt.mock.calls.map(call => call[6].receiveId));
        expect(await h.client.decline(h.event.from_webid, h.event.message_id)).toBe(true);
        expect(h.commits.at(-1).cmd).toBe('native_media_decline');
        expect(h.commits.some(command => command.cmd === 'native_media_commit')).toBe(false);
        expect(h.options.persistFile).not.toHaveBeenCalled(); expect(h.messages).toEqual([]);
        expect(h.journal.size).toBe(0); expect(await h.inbox.list('bob')).toEqual([]);
        expect((await h.receipts.pendingCommits('native-decline:bob')).length).toBe(1);
        expect(await h.client.part(await h.part(1))).toBe(false);
        h.client.stop(); const reopened = h.engine(); const sent = h.sent.length;
        await reopened.offer(h.event);
        expect(h.sent).toHaveLength(sent); expect(h.commits.at(-1).cmd).toBe('native_media_decline');
        expect(new Set(h.e2e.ratchetDecrypt.mock.calls.map(call => call[6].receiveId))).toEqual(keys);
        const command = h.commits.at(-1);
        expect(await h.receiver.confirmDecline({ ...command, type: 'native_media_declined', queue_id: 999 })).toBe(false);
        expect(await h.receiver.confirmDecline({ ...command, type: 'native_media_declined' })).toBe(true);
        expect(await h.receipts.pendingCommits('native-decline:bob')).toEqual([]);
    });
    it('retains ciphertext and the consumed header if decline evidence cannot be stored, and retries the original identity', async () => {
        const h = await harness(); await h.client.offer(h.event);
        const remember = h.receipts.remember;
        h.receipts.remember = vi.fn(async (...args) => args[0] === 'native-decline:bob' ? false : remember(...args));
        expect(await h.client.decline(h.event.from_webid, h.event.message_id)).toBe(false);
        expect(await h.inbox.list('bob')).toHaveLength(1); expect(h.journal.size).toBe(1);
        expect(h.commits.some(command => command.cmd === 'native_media_decline')).toBe(false);
        h.receipts.remember = remember;
        expect(await h.client.decline(h.event.from_webid, h.event.message_id)).toBe(true);
        expect(await h.inbox.list('bob')).toEqual([]);
    });
    it('does not decline another account, wrong original key or a file already durably saved', async () => {
        const h = await harness(); await h.client.offer(h.event);
        h.pub('b'.repeat(43)); expect(await h.client.decline(h.event.from_webid, h.event.message_id)).toBe(false);
        h.pub('a'.repeat(43)); h.scope('other'); expect(await h.client.decline(h.event.from_webid, h.event.message_id)).toBe(false);
        h.scope('bob'); await drain(h);
        expect(await h.client.decline(h.event.from_webid, h.event.message_id)).toBe(false);
        expect(h.messages).toHaveLength(1); expect(h.commits.at(-1).cmd).toBe('native_media_commit');
    });
});

it('does not publish a late part completion or restart downloads after successful decline', async () => {
    const h = await harness(); await h.client.offer(h.event);
    const put = h.inbox.put; let finish;
    h.inbox.put = async (...args) => { const row = await put(...args); await new Promise(resolve => { finish = resolve; }); return row; };
    const part = h.client.part(await h.part(0));
    await vi.waitFor(() => expect(finish).toBeTypeOf('function'));
    expect(await h.client.decline(h.event.from_webid, h.event.message_id)).toBe(true);
    const requests = h.sent.length; finish();
    expect(await part).toBe(false); expect(h.sent).toHaveLength(requests);
    expect(await h.inbox.list('bob')).toEqual([]); expect(h.messages).toEqual([]);
});
