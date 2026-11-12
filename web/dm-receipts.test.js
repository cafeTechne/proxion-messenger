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
    it('keeps bounded content-free locators across reopen and rotates pending pages', async () => {
        const db = new IDBFactory(), store = createDmReceiptStore(db);
        const url = 'https://pod.example/alice/proxion/dm-inbox/drop';
        for (const letter of ['a', 'b', 'c']) await store.remember('scope', letter.repeat(64), url + letter);
        for (const bad of ['https://foreign.example/private/plaintext', url + '?private', url + '#', url + '/..',
            'https://user:password@pod.example/alice/proxion/dm-inbox/drop',
            'https://pod.example/' + 'x'.repeat(2048) + '/proxion/dm-inbox/drop']) {
            await store.remember('scope', 'd'.repeat(64), bad);
        }
        const reopened = createDmReceiptStore(db), first = await reopened.pending('scope', { limit: 2 });
        expect(first.map(row => row.fingerprint[0])).toEqual(['a', 'b']);
        expect(await reopened.pending('scope', { after: 'b'.repeat(64), limit: 2 })).toEqual([{ fingerprint: 'c'.repeat(64), url: url + 'c' }]);
        expect((await reopened.pending('scope', { after: 'f'.repeat(64), limit: 2 }))[0].fingerprint).toBe('a'.repeat(64));
        expect(await reopened.pending('other')).toEqual([]);
        await reopened.remember('scope', 'a'.repeat(64));
        expect((await reopened.pending('scope'))[0].url).toBe(url + 'a');
    });
    it('conditionally acknowledges missing URLs without clearing relocated or legacy receipts', async () => {
        const store = createDmReceiptStore(new IDBFactory(), { max: 1 }), fingerprint = 'a'.repeat(64);
        const url = 'https://pod.example/alice/proxion/dm-inbox/one';
        await store.remember('scope', fingerprint);
        expect(await store.acknowledgeMissing('scope', fingerprint, url)).toBe(false);
        await store.remember('scope', fingerprint, url);
        await store.remember('scope', fingerprint, url + '-relocated');
        expect(await store.acknowledgeMissing('scope', fingerprint, url)).toBe(false);
        expect(await store.hasSpace('scope')).toBe(false);
        expect(await store.acknowledgeMissing('scope', fingerprint, url + '-relocated')).toBe(true);
        expect(await store.hasSpace('scope')).toBe(true);
        expect(await store.has('scope', fingerprint)).toBe(true);
    });
});

function harness({ receipts = createDmReceiptStore(new IDBFactory()), persist = async () => true, fanout = false } = {}) {
    let actor = 'alice', device = 'device-one';
    const envelope = fanout ? { kind: 'fanout', from_webid: 'bob', message_id: 'm', to_device_id: device,
        payload: { e2e: true, content: 'ciphertext' } } : { from_webid: 'bob', message_id: 'm', e2e: true, content: 'ciphertext', nonce: 'n' };
    let drops = [{ url: 'https://pod.example/alice/proxion/dm-inbox/one', envelope }];
    const pod = { podReadDmDrops: vi.fn(async () => drops), podDeleteDmDrop: vi.fn(async () => false) };
    const e2e = { ratchetDecrypt: vi.fn(async () => 'private plaintext'), cachePeerPub: vi.fn() };
    const handleEvent = vi.fn(async () => ({ persisted: true }));
    const options = { pod, e2e, handleEvent, receipts, getSelfWebId: () => actor, getMyDeviceId: () => device,
        verifySender: async () => true, persistMessage: persist };
    return { pod, e2e, handleEvent, envelope, receipts, scope: () => JSON.stringify([actor, device]), engine: () => createWebDm(options),
        setDrops: next => { drops = next; }, device: next => { device = next; }, actor: next => { actor = next; } };
}

describe('DM replay recovery after engine recreation', () => {
    it('does not let an unavailable live-only receipt starve durable reconciliation', async () => {
        const store = createDmReceiptStore(new IDBFactory(), { max: 1 }), h = harness({ receipts: store, persist: async () => false });
        const engine = h.engine(); await engine.drainOnce();
        await store.remember(h.scope(), 'b'.repeat(64), 'https://pod.example/alice/proxion/dm-inbox/durable');
        h.setDrops([]);
        h.pod.podDmDropExists = vi.fn(async url => url.endsWith('/durable') ? false : null);
        await engine.drainOnce(); await engine.drainOnce();
        expect(await store.hasSpace(h.scope())).toBe(true);
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(1);
    });
    it('retains legacy pending receipts without guessing absence from an empty listing', async () => {
        const store = createDmReceiptStore(new IDBFactory(), { max: 1 }), h = harness({ receipts: store });
        await store.remember(h.scope(), 'a'.repeat(64));
        h.setDrops([]); h.pod.podDmDropExists = vi.fn(async () => false);
        await h.engine().drainOnce();
        expect(h.pod.podDmDropExists).not.toHaveBeenCalled();
        expect(await store.hasSpace(h.scope())).toBe(false);
    });
    it('rotates bounded pages beyond unavailable metadata without starving later receipts', async () => {
        const store = createDmReceiptStore(new IDBFactory(), { max: 20 }), h = harness({ receipts: store });
        h.setDrops([]);
        for (let index = 1; index <= 20; index++) await store.remember(h.scope(), index.toString(16).padStart(64, '0'),
            'https://pod.example/alice/proxion/dm-inbox/drop-' + index);
        h.pod.podDmDropExists = vi.fn(async url => url.endsWith('-1') ? null : !url.endsWith('-20'));
        const engine = h.engine();
        for (let scan = 0; scan < 4; scan++) {
            const before = h.pod.podDmDropExists.mock.calls.length;
            await engine.drainOnce();
            expect(h.pod.podDmDropExists.mock.calls.length - before).toBeLessThanOrEqual(8);
        }
        expect(await store.hasSpace(h.scope())).toBe(true);
        expect((await store.pending(h.scope(), { after: '0'.repeat(64), limit: 16 })).some(row => row.url.endsWith('-1'))).toBe(true);
        expect(h.pod.podDeleteDmDrop).not.toHaveBeenCalled();
        expect(h.e2e.ratchetDecrypt).not.toHaveBeenCalled();
    });
    it('releases pending capacity after external deletion and reload without decrypting old ciphertext again', async () => {
        const store = createDmReceiptStore(new IDBFactory(), { max: 1 }), h = harness({ receipts: store });
        await h.engine().drainOnce(); expect(await store.hasSpace(h.scope())).toBe(false);
        const old = await dmEnvelopeFingerprint(h.envelope);
        h.pod.podDmDropExists = vi.fn(async () => false);
        h.setDrops([{ url: 'https://pod.example/alice/proxion/dm-inbox/two', envelope: { ...h.envelope, content: 'new ciphertext' } }]);
        await h.engine().drainOnce();
        expect(h.pod.podDmDropExists).toHaveBeenCalledTimes(1);
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(2);
        expect(await store.has(h.scope(), old)).toBe(false); // acknowledged row was eligible for cap eviction
    });
    it('preserves the exact replay fingerprint after missing-resource acknowledgement when space permits', async () => {
        const h = harness(); await h.engine().drainOnce(); h.setDrops([]);
        h.pod.podDmDropExists = vi.fn(async () => false);
        await h.engine().drainOnce();
        h.setDrops([{ url: 'https://pod.example/alice/proxion/dm-inbox/replay', envelope: h.envelope }]);
        await h.engine().drainOnce();
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(1);
        expect(h.handleEvent).toHaveBeenCalledTimes(1);
    });
    it.each([true, null])('does not free receipt slots when metadata is present or uncertain (%s)', async exists => {
        const store = createDmReceiptStore(new IDBFactory(), { max: 1 }), h = harness({ receipts: store });
        await h.engine().drainOnce(); h.pod.podDmDropExists = vi.fn(async () => exists);
        h.setDrops([{ url: 'inbox/two', envelope: { ...h.envelope, content: 'next' } }]);
        await h.engine().drainOnce();
        expect(await store.hasSpace(h.scope())).toBe(false);
        expect(h.e2e.ratchetDecrypt).toHaveBeenCalledTimes(1);
    });
    it('discards late absence results after account changes', async () => {
        const h = harness(); await h.engine().drainOnce();
        const originalScope = h.scope(); let resolve;
        h.setDrops([]); h.pod.podDmDropExists = vi.fn(() => new Promise(done => { resolve = done; }));
        const pending = h.engine().drainOnce();
        await vi.waitFor(() => expect(resolve).toBeTypeOf('function'));
        h.actor('other-account'); resolve(false); await pending;
        expect((await h.receipts.pending(originalScope))).toHaveLength(1);
    });
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
