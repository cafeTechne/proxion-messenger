import { describe, it, expect, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { createDmReceiptStore, dmEnvelopeFingerprint } from './dm-receipts.js';
import { createWebDm } from './webdm.js';

describe('content-free durable DM receipts', () => {
    it('hashes canonical complete envelopes without trusting message ids or URLs', async () => {
        const envelope = { message_id: 'same', from_webid: 'bob', content: 'cipher', payload: { nonce: 'n', index: 1 } };
        const reordered = { payload: { index: 1, nonce: 'n' }, content: 'cipher', from_webid: 'bob', message_id: 'same' };
        expect(await dmEnvelopeFingerprint(envelope)).toBe(await dmEnvelopeFingerprint(reordered));
        for (const changed of [{ ...envelope, content: 'different' }, { ...envelope, from_webid: 'mallory' },
            { ...envelope, payload: { nonce: 'other', index: 1 } }, { ...envelope, to_device_id: 'other' }]) {
            expect(await dmEnvelopeFingerprint(envelope)).not.toBe(await dmEnvelopeFingerprint(changed));
        }
        await expect(dmEnvelopeFingerprint({ content: 'x'.repeat(131073) })).rejects.toThrow();
    });
    it('persists hashes across reopen, isolates scopes and evicts only acknowledged receipts', async () => {
        const db = new IDBFactory(), store = createDmReceiptStore(db, { max: 2 });
        expect(await store.remember('alice/device1', 'a'.repeat(64))).toBe(true);
        expect(await store.remember('alice/device1', 'b'.repeat(64))).toBe(true);
        expect(await store.hasSpace('alice/device1')).toBe(false);
        expect(await store.remember('alice/device1', 'c'.repeat(64))).toBe(false);
        expect(await store.remember('alice/device2', 'c'.repeat(64))).toBe(true);
        await store.acknowledge('alice/device1', 'b'.repeat(64));
        expect(await store.remember('alice/device1', 'c'.repeat(64))).toBe(true);
        const reopened = createDmReceiptStore(db, { max: 2 });
        expect(await reopened.has('alice/device1', 'a'.repeat(64))).toBe(true);
        expect(await reopened.has('alice/device1', 'b'.repeat(64))).toBe(false);
        expect(await reopened.has('bob/device1', 'a'.repeat(64))).toBe(false);
        const opened = await new Promise(resolve => { const req = db.open('proxion-dm-receipts-v1'); req.onsuccess = () => resolve(req.result); });
        const rows = await new Promise(resolve => { const req = opened.transaction('receipts').objectStore('receipts').getAll(); req.onsuccess = () => resolve(req.result); });
        opened.close();
        expect(rows.every(row => Object.keys(row).sort().join(',') === 'acknowledged,at,fingerprint,key,scope')).toBe(true);
    });
    it('refuses to store plaintext in place of a digest', async () => {
        const store = createDmReceiptStore(new IDBFactory());
        await expect(store.remember('scope', 'private message')).rejects.toThrow('SHA-256');
    });
    it('enforces the pending cap inside concurrent write transactions', async () => {
        const store = createDmReceiptStore(new IDBFactory(), { max: 2 });
        const results = await Promise.all(['a'.repeat(64), 'b'.repeat(64), 'c'.repeat(64)].map(id => store.remember('scope', id)));
        expect(results.filter(Boolean)).toHaveLength(2);
    });
});

function harness({ receipts = createDmReceiptStore(new IDBFactory()), persist = async () => true, fanout = false } = {}) {
    let actor = 'alice', device = 'device-one';
    const envelope = fanout ? { kind: 'fanout', from_webid: 'bob', message_id: 'm', to_device_id: device,
        payload: { e2e: true, content: 'ciphertext' } } : { from_webid: 'bob', message_id: 'm', e2e: true, content: 'ciphertext', nonce: 'n' };
    let drops = [{ url: 'inbox/one', envelope }];
    const pod = { podReadDmDrops: vi.fn(async () => drops), podDeleteDmDrop: vi.fn(async () => false) };
    const e2e = { ratchetDecrypt: vi.fn(async () => 'private plaintext'), cachePeerPub: vi.fn() };
    const handleEvent = vi.fn(async () => ({ persisted: true }));
    const options = { pod, e2e, handleEvent, receipts, getSelfWebId: () => actor, getMyDeviceId: () => device,
        verifySender: async () => true, persistMessage: persist };
    return { pod, e2e, handleEvent, envelope, engine: () => createWebDm(options),
        setDrops: next => { drops = next; }, device: next => { device = next; }, actor: next => { actor = next; } };
}

describe('DM replay recovery after engine recreation', () => {
    it.each([false, true])('retries deletion after reload without repeating delivery/decryption (fanout=%s)', async fanout => {
        const h = harness({ fanout });
        await h.engine().drainOnce();
        h.setDrops([{ url: 'inbox/new-url', envelope: { ...h.envelope } }]);
        const reloaded = h.engine();
        await reloaded.drainOnce();
        expect(h.handleEvent).toHaveBeenCalledTimes(1);
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(fanout ? 0 : 1);
        expect(h.pod.podDeleteDmDrop).toHaveBeenCalledTimes(2);
        h.pod.podDeleteDmDrop.mockResolvedValue(true);
        await reloaded.drainOnce();
        expect(h.pod.podDeleteDmDrop).toHaveBeenCalledTimes(3);
        expect(h.handleEvent).toHaveBeenCalledTimes(1);
    });
    it('does not suppress fresh ciphertext reusing a URL and message id', async () => {
        const h = harness(), engine = h.engine();
        await engine.drainOnce();
        h.setDrops([{ url: 'inbox/one', envelope: { ...h.envelope, content: 'fresh retry' } }]);
        await engine.drainOnce();
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(2);
    });
    it('does not apply receipts across an account or device change', async () => {
        const h = harness(); await h.engine().drainOnce();
        h.actor('another account'); await h.engine().drainOnce();
        h.device('another device'); await h.engine().drainOnce();
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(3);
    });
    it('remains usable when IndexedDB fails and avoids durable claims when history fails', async () => {
        const receipts = { has: vi.fn(async () => { throw Error('offline storage'); }), hasSpace: vi.fn(), remember: vi.fn(async () => false) };
        const h = harness({ receipts }); await h.engine().drainOnce();
        expect(h.handleEvent).toHaveBeenCalledTimes(1);
        const store = createDmReceiptStore(new IDBFactory());
        const failed = harness({ receipts: store, persist: async () => false });
        await failed.engine().drainOnce(); await failed.engine().drainOnce();
        expect(failed.e2e.ratchetDecrypt).toHaveBeenCalledTimes(2);
    });
    it('leaves undecryptable fanout and uncommitted fanout history unreceipted', async () => {
        const h = harness({ fanout: true });
        h.handleEvent.mockResolvedValue(false);
        await h.engine().drainOnce();
        expect(h.pod.podDeleteDmDrop).not.toHaveBeenCalled();
        h.handleEvent.mockResolvedValue({ persisted: false });
        await h.engine().drainOnce(); await h.engine().drainOnce();
        expect(h.handleEvent).toHaveBeenCalledTimes(3);
    });
    it('applies pending-receipt backpressure before decrypting any new envelopes', async () => {
        const store = createDmReceiptStore(new IDBFactory(), { max: 1 }), h = harness({ receipts: store });
        await h.engine().drainOnce();
        h.setDrops([{ url: 'inbox/two', envelope: { ...h.envelope, content: 'next' } }]);
        await h.engine().drainOnce();
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(1);
    });
});
