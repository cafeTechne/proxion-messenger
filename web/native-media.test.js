import { describe, it, expect, vi, afterEach } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { createNativeMedia } from './native-media.js';
import { createNativeMediaStore } from './native-media-drafts.js';
import { createDmReceiptStore } from './dm-receipts.js';
import { createSecondaryCrypto } from './e2e-secondary.js';

const pub = 'a'.repeat(43), view = { type: 'dm', id: 'thread', peerWebid: 'did:key:bob' };
const instances = [];
afterEach(() => { for (const engine of instances.splice(0)) engine.stop(); });
function harness(overrides = {}) {
    let scope = 'alice', generation = 0;
    const db = new IDBFactory(), store = createNativeMediaStore(db), sent = [], errors = [];
    const encrypt = vi.fn(async () => ({ ciphertext: 'header', nonce: 'nonce', ratchetPub: 'ratchet', msgNum: 0, pn: 0 }));
    const options = { store, receipts: createDmReceiptStore(db), getScope: () => scope,
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
