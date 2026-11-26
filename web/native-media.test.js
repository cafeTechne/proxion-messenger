import { describe, it, expect, vi, afterEach } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { createNativeMedia } from './native-media.js';
import { createNativeMediaStore } from './native-media-drafts.js';
import { createNativeMediaInbox } from './native-media-inbox.js';
import { dmEnvelopeFingerprint } from './dm-receipts.js';
import { createDmReceiptStore } from './dm-receipts.js';
import { createSecondaryCrypto } from './e2e-secondary.js';

const pub = 'a'.repeat(43), view = { type: 'dm', id: 'thread', peerWebid: 'did:key:bob' };
const instances = [];
afterEach(() => { for (const engine of instances.splice(0)) engine.stop(); });
function harness(overrides = {}) {
    let scope = 'alice', generation = 0;
    const db = new IDBFactory(), store = createNativeMediaStore(db, { maxCipherBytes: 25 * 1024 * 1024 }), sent = [], errors = [];
    const encrypt = vi.fn(async () => ({ ciphertext: 'header', nonce: 'nonce', ratchetPub: 'ratchet', msgNum: 0, pn: 0 }));
    const options = { store, inbox: createNativeMediaInbox(db), receipts: createDmReceiptStore(db), getScope: () => scope,
        getGeneration: () => generation, isActive: () => true, getPeerPub: () => pub,
        secondary: createSecondaryCrypto({ encrypt, isKeyed: () => true, isSelf: () => false }),
        e2e: { pendingDmReceives: vi.fn(async () => []), releaseDmSendHold: vi.fn(async () => true), finishDmSend: vi.fn(async () => true) },
        persistMessage: vi.fn(async () => true), persistOwnMessage: vi.fn(async () => true),
        handleEvent: vi.fn(), toMessage: vi.fn(), send: vi.fn(command => { sent.push(command); return true; }),
        onError: error => { errors.push(error); }, ...overrides };
    const engine = createNativeMedia(options); instances.push(engine);
    const enqueue = () => engine.sender.enqueue(new Blob(['private bytes']), view, { name: 'private.txt', mime: 'text/plain' }, 'file-1');
    const settle = () => new Promise(resolve => setTimeout(resolve, 0));
    const reply = request => ({ type: 'native_media_capability_result', v: 1, request_id: request.request_id,
        peer_webid: view.peerWebid, expected_pub: pub, peer_pub: pub, supported: true });
    return { engine, store, options, encrypt, sent, errors, enqueue, settle, reply,
        account: () => { scope = 'other'; generation++; } };
}

describe('native media live socket lifecycle', () => {
    it('reports inaccessible native storage without rejecting other pending-send providers', async () => {
        const h = harness(); h.engine.start(1); await h.engine.recover();
        vi.spyOn(h.store, 'list').mockRejectedValueOnce(Error('native storage blocked'));
        expect(await h.engine.inspect()).toEqual([{ id: 'native-in:storage', peer: '', state: 'nativeStorage', canCancel: false, canRetry: true }]);
        expect(h.errors).toHaveLength(1);
    });
    it('shows received files waiting on verification/history only in the original account with retry and no cancellation', async () => {
        const h = harness(); h.engine.start(1); await h.engine.recover();
        h.options.e2e.pendingDmReceives.mockResolvedValue([
            { receiveId: 'a'.repeat(64), peerId: 'did:key:bob', data: { scope: 'alice', timestamp: '2026-11-22T00:00:00Z' } },
            { receiveId: 'b'.repeat(64), peerId: 'did:key:other', data: { scope: 'other' } },
        ]);
        expect(await h.engine.inspect()).toEqual([{ id: 'native-in:' + 'a'.repeat(64), peer: 'did:key:bob',
            timestamp: '2026-11-22T00:00:00Z', canCancel: false, canRetry: true, state: 'nativeIncoming' }]);
    });
    it('does not make a linked or pod outbox unavailable when native media is inactive', async () => {
        const h = harness({ isActive: () => false });
        expect(await h.engine.inspect()).toEqual([]);
        expect(h.options.e2e.pendingDmReceives).not.toHaveBeenCalled();
    });
    it('replays on return to the foreground and removes the listener when stopped', async () => {
        const listeners = new Set(), doc = { hidden: true,
            addEventListener: vi.fn((type, listener) => listeners.add(listener)),
            removeEventListener: vi.fn((type, listener) => listeners.delete(listener)) };
        const h = harness({ doc }); h.engine.start(1); await h.engine.recover();
        expect(h.sent.filter(row => row.cmd === 'native_media_recover')).toHaveLength(1);
        doc.hidden = false; for (const listener of listeners) listener(); await h.engine.recover();
        expect(h.sent.filter(row => row.cmd === 'native_media_recover')).toHaveLength(2);
        h.engine.stop(); expect(listeners.size).toBe(0);
    });
    it('allows safe unspent source cancellation while the transport is no longer negotiated', async () => {
        const h = harness(); h.engine.start(1); await h.engine.recover(); await h.enqueue(); h.engine.stop();
        expect((await h.engine.inspect())[0]).toMatchObject({ state: 'nativeConnection', canCancel: true, canRetry: false });
        expect(await h.engine.cancel('file-1')).toBe(true);
        expect(await h.store.list('alice')).toEqual([]); expect(h.encrypt).not.toHaveBeenCalled();
    });
    it('does not enable unnegotiated peers and sends one bounded recovery request per scan', async () => {
        const h = harness(); h.engine.start(undefined); expect(h.engine.available()).toBe(false);
        expect(await h.engine.handleEvent({ type: 'native_media_status' })).toBe(false);
        h.engine.start(1); await h.engine.recover();
        expect(h.sent.filter(command => command.cmd === 'native_media_recover')).toEqual([
            { cmd: 'native_media_recover', message_ids: [] }]);
    });
    it('binds capability replies to the original request, peer and key before spending a header', async () => {
        const h = harness(); h.engine.start(1); await h.engine.recover(); await h.enqueue();
        const retry = h.engine.sender.retry('file-1');
        await vi.waitFor(() => expect(h.sent.some(command => command.cmd === 'native_media_capability')).toBe(true));
        const request = h.sent.find(command => command.cmd === 'native_media_capability');
        await h.engine.handleEvent({ ...h.reply(request), peer_webid: 'did:key:foreign' });
        await h.settle(); expect(h.encrypt).not.toHaveBeenCalled();
        await h.engine.handleEvent(h.reply(request)); expect(await retry).toBe(true);
        expect(h.encrypt).toHaveBeenCalledTimes(1);
        expect(h.sent.at(-1)).toMatchObject({ cmd: 'send_file', native_media_v: 1, message_id: 'file-1' });
        expect((await h.engine.inspect())[0]).toMatchObject({ id: 'native:file-1', state: 'nativeHeader', canCancel: false });
    });
    it('retains an unavailable-peer source and allows only that unspent source to be cancelled', async () => {
        const h = harness(); h.engine.start(1); await h.engine.recover(); await h.enqueue();
        const retry = h.engine.sender.retry('file-1');
        await vi.waitFor(() => expect(h.sent.some(command => command.cmd === 'native_media_capability')).toBe(true));
        const request = h.sent.find(command => command.cmd === 'native_media_capability');
        await h.engine.handleEvent({ ...h.reply(request), supported: false, peer_pub: null });
        expect(await retry).toBe(false); expect(h.encrypt).not.toHaveBeenCalled();
        expect((await h.engine.inspect())[0]).toMatchObject({ state: 'nativePeer', canCancel: true });
        expect(await h.engine.cancel('file-1')).toBe(true); expect(await h.store.list('alice')).toEqual([]);
    });
    it('cancels capability continuations on reconnect and rejects stale account replies', async () => {
        const h = harness(); h.engine.start(1); await h.engine.recover(); await h.enqueue();
        const retry = h.engine.sender.retry('file-1');
        await vi.waitFor(() => expect(h.sent.some(command => command.cmd === 'native_media_capability')).toBe(true));
        const request = h.sent.find(command => command.cmd === 'native_media_capability');
        h.engine.stop(); expect(await retry).toBe(false);
        h.account(); h.engine.start(1); await h.engine.recover();
        await h.engine.handleEvent(h.reply(request)); expect(h.encrypt).not.toHaveBeenCalled();
        expect((await h.store.get('alice', 'file-1')).stage).toBe('source');
    });
    it('does not cancel a source after its transactional preparation has won the race', async () => {
        const h = harness(); h.engine.start(1); await h.engine.recover(); await h.enqueue();
        const retry = h.engine.sender.retry('file-1');
        await vi.waitFor(() => expect(h.sent.some(command => command.cmd === 'native_media_capability')).toBe(true));
        await h.engine.handleEvent(h.reply(h.sent.find(command => command.cmd === 'native_media_capability')));
        await retry;
        expect(await h.engine.cancel('file-1')).toBe(false);
        expect((await h.store.get('alice', 'file-1')).stage).toBe('sealed');
    });
});


describe('native chunk socket integration', () => {
    it('negotiates the original peer key and complete chunk capability before uploading durable large bytes', async () => {
        const h = harness({ getOwnPub: () => pub, persistFile: vi.fn(async () => true) });
        h.engine.start(1, 1); await h.engine.recover();
        await h.engine.sender.enqueue(new Blob([new Uint8Array(2 * 1024 * 1024)]), view,
            { name: 'large.txt', mime: 'text/plain' }, 'large');
        const retry = h.engine.sender.retry('large');
        await vi.waitFor(() => expect(h.sent.some(command => command.cmd === 'native_media_capability')).toBe(true));
        const request = h.sent.find(command => command.cmd === 'native_media_capability');
        expect(request.native_chunks_v).toBe(1);
        await h.engine.handleEvent(h.reply(request)); await h.settle(); expect(h.encrypt).not.toHaveBeenCalled();
        await h.engine.handleEvent({ ...h.reply(request), native_chunks_v: 1 }); expect(await retry).toBe(true);
        const begin = h.sent.find(command => command.cmd === 'native_media_begin');
        expect(begin.manifest.size).toBe(2 * 1024 * 1024 + 16);
        expect(h.sent.some(command => command.cmd === 'send_file')).toBe(false);
        const row = await h.store.get('alice', 'large');
        const { nativeMediaSendIdentity } = await import('./native-media-send.js'), identity = await nativeMediaSendIdentity(row);
        await h.engine.handleEvent({ type: 'native_media_parts', v: 1, message_id: 'large',
            media_digest: identity.mediaDigest, header_digest: identity.headerDigest,
            manifest_digest: await dmEnvelopeFingerprint(begin.manifest), recipient_pub: pub,
            custody_complete: false, header_committed: false, committed: false, missing: [0, 1, 2] });
        expect(h.sent.at(-1)).toMatchObject({ cmd: 'native_media_part', message_id: 'large', seq: 0 });
        expect(h.sent.at(-1).data_b64.length).toBeLessThanOrEqual(87384);
        expect(h.options.e2e.releaseDmSendHold).not.toHaveBeenCalled();
        expect((await h.store.get('alice', 'large')).committed).toBeUndefined();
        h.engine.stop();
        expect(await h.engine.handleEvent({ type: 'native_media_parts' })).toBe(false);
    });
    it('does not prepare a large draft when the negotiated connection supports only inline media', async () => {
        const h = harness({ getOwnPub: () => pub, persistFile: vi.fn(async () => true) });
        h.engine.start(1); await h.engine.recover();
        await h.engine.sender.enqueue(new Blob([new Uint8Array(600000)]), view,
            { name: 'waiting.txt', mime: 'text/plain' }, 'large');
        expect(h.engine.chunksAvailable()).toBe(false);
        expect(await h.engine.sender.retry('large')).toBe(false);
        expect(h.encrypt).not.toHaveBeenCalled();
        expect(h.sent.some(command => command.cmd === 'native_media_begin' || command.cmd === 'send_file')).toBe(false);
        expect((await h.store.get('alice', 'large')).stage).toBe('source');
    });
});

it('inspects durable chunk progress before header consumption and keeps it visible while disconnected', async () => {
    const h = harness({ getOwnPub: () => pub, persistFile: vi.fn(async () => true) });
    h.engine.start(1, 1); await h.engine.recover();
    const manifest = { e2e: true, e2e_v: 1, content: 'header', nonce: 'nonce', ratchet_pub: pub, msg_num: 0, pn: 0,
        size: 100016, cipher_digest: 'a'.repeat(64), chunk_count: 2, recipient_pub: pub };
    const offer = { ...manifest, type: 'native_media_offer', native_chunks_v: 1, from_webid: 'did:key:bob', message_id: 'incoming',
        manifest_digest: await dmEnvelopeFingerprint(manifest), native_commit: { v: 1, queue_id: 1, envelope_digest: 'b'.repeat(64) } };
    await h.options.inbox.begin('alice', offer);
    await h.options.inbox.begin('other', { ...offer, message_id: 'foreign' });
    h.options.e2e.pendingDmReceives.mockResolvedValue([{ receiveId: 'header', peerId: 'did:key:bob',
        data: { scope: 'alice', commit: { message_id: 'incoming' } } }]);
    const pending = await h.engine.inspect();
    expect(pending).toHaveLength(1);
    expect(pending[0]).toMatchObject({ state: 'nativeIncoming', peer: 'did:key:bob', progress: { received: 0, total: 2 }, canCancel: false, canRetry: true });
    h.engine.stop();
    expect((await h.engine.inspect())[0]).toMatchObject({ state: 'nativeConnection', progress: { received: 0, total: 2 }, canCancel: false, canRetry: false });
});


it('scopes bounded feedback subscriptions and detaches them at connection stop', async () => {
    const h = harness(); h.engine.start(1); await h.engine.recover();
    const listener = vi.fn(), stop = h.engine.subscribe(listener);
    h.engine.stop(); expect(listener).toHaveBeenCalledWith({ reset: true });
    h.engine.start(1); await h.engine.recover(); h.engine.stop(); expect(listener).toHaveBeenCalledTimes(1); stop();
    h.engine.start(1); const former = vi.fn(); h.engine.subscribe(former); h.account(); h.engine.stop();
    expect(former).not.toHaveBeenCalled();
});


it('does not reuse another account’s feedback when local transfer ids coincide', async () => {
    const h = harness(); h.engine.start(1); await h.engine.recover(); await h.enqueue();
    const attempt = h.engine.sender.retry('file-1'); await h.settle();
    const request = h.sent.find(row => row.cmd === 'native_media_capability');
    await h.engine.handleEvent({ ...h.reply(request), supported: false }); await attempt;
    expect((await h.engine.inspect())[0].state).toBe('nativePeer');
    h.account();
    await h.store.insert({ scope: 'other', id: 'file-1', peer: view.peerWebid, view, blob: new Blob(['other private source']),
        metadata: { name: 'other.txt', mime: 'text/plain' }, receiveHold: true });
    expect((await h.engine.inspect())[0].state).toBe('nativeWaiting');
});
