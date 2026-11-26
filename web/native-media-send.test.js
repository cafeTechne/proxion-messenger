import { describe, it, expect, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { createNativeMediaStore } from './native-media-drafts.js';
import { createSecondaryCrypto } from './e2e-secondary.js';
import { createNativeMediaSender, nativeMediaSendIdentity } from './native-media-send.js';

const peerPub = 'a'.repeat(43);
async function harness() {
    let scope = 'alice/device', pub = peerPub;
    const store = createNativeMediaStore(new IDBFactory()), encrypted = new Map(), holds = new Map(), order = [];
    const encrypt = vi.fn(async (peer, plain, opts) => {
        if (!opts.current()) throw Error('stale context');
        if (!encrypted.has(opts.sendId)) {
            encrypted.set(opts.sendId, { ciphertext: 'cipher-' + encrypted.size, nonce: 'nonce',
                msgNum: encrypted.size, pn: 0, ratchetPub: 'pub' });
            holds.set(opts.sendId, 'waiting');
        }
        expect(opts.holdForReceive).toBe(true); expect(opts.peerPub).toBe(peerPub);
        return encrypted.get(opts.sendId);
    });
    const secondary = createSecondaryCrypto({ encrypt, isKeyed: () => true, isSelf: () => false });
    const e2e = {
        releaseDmSendHold: vi.fn(async (peer, id, current) => {
            if (!current() || !holds.has(id)) return false;
            holds.set(id, 'received'); order.push('release'); return true;
        }),
        finishDmSend: vi.fn(async (peer, id, current) => {
            if (!current() || (encrypted.has(id) && holds.get(id) !== 'received')) return false;
            encrypted.delete(id); holds.delete(id); order.push('finish'); return true;
        }),
    };
    const options = { store, secondary, e2e, getScope: () => scope, getPeerPub: () => pub,
        capability: vi.fn(async () => ({ supported: true, peer_pub: peerPub })),
        send: vi.fn(async () => true), persistOwnMessage: vi.fn(async () => { order.push('history'); return true; }), onState: vi.fn() };
    const engine = () => createNativeMediaSender(options), sender = engine();
    await sender.enqueue(new Blob(['source bytes']), { type: 'dm', id: 'thread', peerWebid: 'did:key:bob' },
        { name: 'private.txt', mime: 'text/plain' }, 'file-1');
    const status = async (patch = {}) => {
        const row = await store.get('alice/device', 'file-1'), identity = await nativeMediaSendIdentity(row);
        return { type: 'native_media_status', v: 1, message_id: 'file-1', media_digest: identity.mediaDigest,
            header_digest: identity.headerDigest, header_committed: false, status: 'pending', ...patch };
    };
    return { store, options, sender, engine, status, holds, encrypted, encrypt, order,
        account: () => { scope = 'other/device'; }, key: () => { pub = 'b'.repeat(43); } };
}

describe('native media sender evidence boundaries', () => {
    it('retains blocked prepared sends across reload without releasing or silently retrying their spent header', async () => {
        const h = await harness(); await h.sender.retry('file-1');
        expect(await h.sender.status(await h.status({ status: 'blocked' }))).toBe(true);
        expect((await h.store.get('alice/device', 'file-1')).blocked).toBe(true);
        expect(h.options.e2e.releaseDmSendHold).not.toHaveBeenCalled();
        const sent = h.options.send.mock.calls.length;
        expect(await h.engine().retry('file-1')).toBe(false);
        expect(h.options.send).toHaveBeenCalledTimes(sent);
        expect(h.options.e2e.finishDmSend).not.toHaveBeenCalled();
    });
    it('continues unrelated saved files after an earlier recovery attempt fails', async () => {
        const h = await harness(); h.options.onError = vi.fn();
        const engine = h.engine();
        await engine.enqueue(new Blob(['other source']), { type: 'dm', id: 'other-thread', peerWebid: 'did:key:charlie' },
            { name: 'other.txt', mime: 'text/plain' }, 'file-2');
        h.options.capability.mockRejectedValueOnce(Error('first peer interrupted'));
        expect(await engine.recover()).toBe(true);
        expect(h.options.onError).toHaveBeenCalledTimes(1);
        expect((await h.store.get('alice/device', 'file-1')).stage).toBe('source');
        expect((await h.store.get('alice/device', 'file-2')).stage).toBe('sealed');
        expect(h.options.send.mock.calls.some(([command]) => command.cmd === 'send_file' && command.message_id === 'file-2')).toBe(true);
    });
    it('leaves an unsupported/offline peer source unspent and pins its original key', async () => {
        const h = await harness(); h.options.capability.mockResolvedValue({ supported: false });
        expect(await h.sender.retry('file-1')).toBe(false);
        expect(h.encrypt).not.toHaveBeenCalled(); expect(h.options.send).not.toHaveBeenCalled();
        const row = await h.store.get('alice/device', 'file-1'); expect(row.stage).toBe('source'); expect(row.peerPub).toBe(peerPub);
        h.key(); expect(await h.sender.retry('file-1')).toBe(false); expect(h.encrypt).not.toHaveBeenCalled();
    });
    it('reopens and republishes the same header and ciphertext after a lost socket write', async () => {
        const h = await harness(); h.options.send.mockResolvedValueOnce(false);
        expect(await h.sender.retry('file-1')).toBe(false);
        const first = h.options.send.mock.calls[0][0];
        expect(first).toMatchObject({ cmd: 'send_file', native_media_v: 1, message_id: 'file-1', filename: 'file' });
        expect(await h.engine().retry('file-1')).toBe(true);
        expect(h.options.send.mock.calls[1][0]).toEqual(first); expect(h.encrypt).toHaveBeenCalledTimes(1);
        expect([...h.holds.values()]).toEqual(['waiting']);
        expect(h.options.capability).toHaveBeenCalledTimes(1);
    });
    it('does not release a header on local or signed peer gateway custody alone', async () => {
        const h = await harness(); await h.sender.retry('file-1');
        expect(await h.sender.status(await h.status())).toBe(true);
        expect(await h.sender.status(await h.status({ status: 'forwarded' }))).toBe(true);
        expect(h.options.e2e.releaseDmSendHold).not.toHaveBeenCalled();
        expect(h.options.e2e.finishDmSend).not.toHaveBeenCalled();
        expect((await h.store.get('alice/device', 'file-1')).stage).toBe('sealed');
    });
    it('releases only an exact header proof and retains source until full file commit and own history', async () => {
        const h = await harness(); await h.sender.retry('file-1');
        expect(await h.sender.status(await h.status({ header_committed: true, header_digest: 'b'.repeat(64) }))).toBe(false);
        expect(await h.sender.status(await h.status({ header_committed: true, media_digest: 'b'.repeat(43) }))).toBe(false);
        expect(h.options.e2e.releaseDmSendHold).not.toHaveBeenCalled();
        expect(await h.sender.status(await h.status({ header_committed: true }))).toBe(true);
        expect((await h.store.get('alice/device', 'file-1')).headerCommitted).toBe(true);
        expect(h.options.e2e.finishDmSend).not.toHaveBeenCalled();
        const committed = await h.status({ header_committed: true, status: 'committed' });
        expect(await h.sender.status(committed)).toBe(true);
        expect(h.order).toEqual(['release', 'release', 'history', 'finish']);
        expect(await h.store.get('alice/device', 'file-1')).toBeNull();
    });
    it('persists peer progress before release and retries source history after reload', async () => {
        const h = await harness(); await h.sender.retry('file-1');
        h.options.persistOwnMessage.mockResolvedValueOnce(false);
        const committed = await h.status({ header_committed: true, status: 'committed' });
        expect(await h.sender.status(committed)).toBe(false);
        const row = await h.store.get('alice/device', 'file-1'); expect(row.committed).toBe(true); expect(row.historySaved).toBeUndefined();
        expect(h.options.e2e.finishDmSend).not.toHaveBeenCalled();
        expect(await h.engine().retry('file-1')).toBe(true);
        expect(await h.store.get('alice/device', 'file-1')).toBeNull();
    });
    it('never releases the ratchet if the proof cannot be saved', async () => {
        const h = await harness(); await h.sender.retry('file-1');
        const proof = await h.status({ header_committed: true });
        vi.spyOn(h.store, 'progress').mockRejectedValueOnce(Error('quota'));
        await expect(h.sender.status(proof)).rejects.toThrow('quota');
        expect(h.options.e2e.releaseDmSendHold).not.toHaveBeenCalled();
        expect((await h.store.get('alice/device', 'file-1')).headerCommitted).toBeUndefined();
        expect(await h.engine().status(proof)).toBe(true);
    });
    it('retries failed source deletion after ratchet cleanup without trying to release a vanished hold', async () => {
        const h = await harness(); await h.sender.retry('file-1');
        const proof = await h.status({ header_committed: true, status: 'committed' });
        const remove = vi.spyOn(h.store, 'removeCompleted'); remove.mockRejectedValueOnce(Error('blocked database'));
        await expect(h.sender.status(proof)).rejects.toThrow('blocked database');
        expect(h.encrypted.size).toBe(0); expect(h.holds.size).toBe(0);
        expect((await h.store.get('alice/device', 'file-1')).historySaved).toBe(true);
        const releases = h.options.e2e.releaseDmSendHold.mock.calls.length;
        expect(await h.engine().retry('file-1')).toBe(true);
        expect(h.options.e2e.releaseDmSendHold).toHaveBeenCalledTimes(releases);
        expect(await h.store.get('alice/device', 'file-1')).toBeNull();
    });
    it('invalidates capability continuations on stop or account switch before any key spend', async () => {
        const h = await harness(); h.options.capability.mockImplementation(async () => { h.sender.stop(); return { supported: true, peer_pub: peerPub }; });
        expect(await h.sender.retry('file-1')).toBe(false); expect(h.encrypt).not.toHaveBeenCalled();
        const next = h.engine(); h.options.capability.mockImplementation(async () => { h.account(); return { supported: true, peer_pub: peerPub }; });
        expect(await next.retry('file-1')).toBe(false); expect(h.encrypt).not.toHaveBeenCalled();
    });
    it('retains the original prepared bytes and send id if stopped after header encryption', async () => {
        const h = await harness(), original = h.encrypt.getMockImplementation();
        h.encrypt.mockImplementation(async (...args) => { const value = await original(...args); h.sender.stop(); return value; });
        await expect(h.sender.retry('file-1')).rejects.toThrow('context changed');
        const interrupted = await h.store.get('alice/device', 'file-1'); expect(interrupted.stage).toBe('prepared');
        expect(h.options.send).not.toHaveBeenCalled(); expect(h.encrypted.size).toBe(1);
        const sendId = [...h.encrypted.keys()][0]; h.encrypt.mockImplementation(original);
        expect(await h.engine().retry('file-1')).toBe(true);
        const resumed = await h.store.get('alice/device', 'file-1');
        expect(resumed.prepared).toEqual(interrupted.prepared); expect([...h.encrypted.keys()]).toEqual([sendId]);
    });
    it('refuses premature source deletion and stale completion writes', async () => {
        const h = await harness(); await h.sender.retry('file-1');
        const row = await h.store.get('alice/device', 'file-1');
        await expect(h.store.removeCompleted(row.scope, row.id, row.revision)).rejects.toThrow('not durable');
        await expect(h.store.progress(row.scope, row.id, row.revision, { committed: true })).rejects.toThrow('Invalid');
        const updated = await h.store.progress(row.scope, row.id, row.revision, { headerCommitted: true });
        await expect(h.store.progress(row.scope, row.id, row.revision, { headerCommitted: true })).rejects.toThrow('Invalid');
        expect(updated.headerCommitted).toBe(true);
    });
});

describe('native sender chunk upload integration boundary', () => {
    it('requires exact chunk readiness before spending a large source header', async () => {
        const h = await harness(); h.options.chunkUpload = vi.fn(async () => true);
        const sender = h.engine();
        await sender.enqueue(new Blob([new Uint8Array(100000)]), { type: 'dm', id: 'thread', peerWebid: 'did:key:bob' },
            { name: 'large.txt', mime: 'text/plain' }, 'large');
        expect(await sender.retry('large')).toBe(false);
        expect(h.options.capability.mock.calls[0][0]).toMatchObject({ native_chunks_v: 1 });
        expect(h.encrypt).not.toHaveBeenCalled(); expect(h.options.chunkUpload).not.toHaveBeenCalled();
        h.options.capability.mockResolvedValue({ supported: true, peer_pub: peerPub, native_chunks_v: 1 });
        expect(await sender.retry('large')).toBe(true);
        const first = h.options.chunkUpload.mock.calls[0];
        expect(first[0].stage).toBe('sealed'); expect(first[0].prepared.ct.length).toBe(100016);
        expect(h.options.send).not.toHaveBeenCalled(); expect(h.options.e2e.releaseDmSendHold).not.toHaveBeenCalled();
        await h.engine().retry('large');
        expect(h.options.chunkUpload.mock.calls[1][0].hdr).toEqual(first[0].hdr);
        expect(h.options.chunkUpload.mock.calls[1][0].prepared).toEqual(first[0].prepared);
        expect(h.encrypt).toHaveBeenCalledTimes(1);
    });
    it('keeps small inline files on the existing route when chunk upload is available', async () => {
        const h = await harness(); h.options.chunkUpload = vi.fn();
        await h.engine().retry('file-1');
        expect(h.options.chunkUpload).not.toHaveBeenCalled();
        expect(h.options.send.mock.calls[0][0].cmd).toBe('send_file');
    });
});

it('retains a declined prepared source and releases only the proven header hold without retransmitting', async () => {
    const h = await harness(); await h.sender.retry('file-1');
    const before = await h.store.get('alice/device', 'file-1');
    const event = await h.status({ status: 'declined', header_committed: true });
    expect(await h.sender.status(event)).toBe(true);
    const retained = await h.store.get('alice/device', 'file-1');
    expect(retained.declined).toBe(true); expect(retained.headerCommitted).toBe(true);
    expect(retained.prepared).toEqual(before.prepared); expect(retained.hdr).toEqual(before.hdr);
    expect(h.options.e2e.releaseDmSendHold).toHaveBeenCalled();
    expect(h.options.e2e.finishDmSend).not.toHaveBeenCalled(); expect(h.options.persistOwnMessage).not.toHaveBeenCalled();
    const count = h.options.send.mock.calls.length;
    expect(await h.engine().retry('file-1')).toBe(false); expect(h.options.send).toHaveBeenCalledTimes(count);
    expect(await h.sender.status({ ...event, status: 'committed' })).toBe(false);
    expect((await h.store.get('alice/device', 'file-1')).committed).toBeUndefined();
});


it('attributes inline refusal only to an active original attempt and never fabricates progress', async () => {
    const h = await harness(); h.options.onState = vi.fn();
    // Recreate to capture the feedback callback supplied to this sender.
    const sender = h.engine(); await sender.retry('file-1');
    const error = { type: 'error', message: 'native_media_unavailable', cmd: 'send_file', message_id: 'file-1' };
    expect(sender.refuse({ ...error, message_id: 'other' })).toBe(false);
    expect(sender.refuse({ ...error, cmd: 'native_media_begin' })).toBe(false);
    expect(sender.refuse(error)).toBe(true);
    expect(h.options.onState).toHaveBeenCalledWith('file-1', 'gateway-storage');
    const row = await h.store.get('alice/device', 'file-1'); expect(row.committed).not.toBe(true);
    expect(row.stage).toBe('sealed'); expect(h.encrypted.size).toBe(1);
    h.account(); expect(sender.refuse(error)).toBe(false);
});
