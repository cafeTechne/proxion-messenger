import { describe, it, expect, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { dmEnvelopeFingerprint } from './dm-receipts.js';
import { createNativeMediaInbox, NATIVE_CHUNK_BYTES, NATIVE_HEADER_KEYS } from './native-media-inbox.js';
const hash = async bytes => [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(byte => byte.toString(16).padStart(2, '0')).join('');
async function offer(size = NATIVE_CHUNK_BYTES + 17, id = 'file') {
    const bytes = new Uint8Array(size).fill(65), manifest = { e2e: true, e2e_v: 1, content: 'header',
        nonce: 'nonce', ratchet_pub: 'pub', msg_num: 0, pn: 0, size,
        cipher_digest: await hash(bytes), chunk_count: Math.ceil(size / NATIVE_CHUNK_BYTES), recipient_pub: 'a'.repeat(43) };
    const event = { ...manifest, type: 'native_media_offer', native_chunks_v: 1, from_webid: 'did:key:alice', message_id: id,
        thread_id: 'thread', manifest_digest: await dmEnvelopeFingerprint(manifest), native_commit: { v: 1, queue_id: 1, envelope_digest: 'a'.repeat(64) } };
    const receiveId = await dmEnvelopeFingerprint({ transport: 'native-media', from_webid: event.from_webid, message_id: id,
        header: Object.fromEntries(NATIVE_HEADER_KEYS.map(key => [key, manifest[key]])), ciphertext_digest: manifest.cipher_digest });
    return { event, bytes, receiveId };
}
async function part(store, item, seq, bytes = item.bytes.subarray(seq * NATIVE_CHUNK_BYTES, (seq + 1) * NATIVE_CHUNK_BYTES), current) {
    return store.put('bob', item.event.from_webid, item.event.message_id, item.event.manifest_digest, seq, bytes, await hash(bytes), current);
}
describe('durable native ciphertext inbox', () => {
    it('reopens exact partial bytes, requests only missing parts and assembles the ordered original', async () => {
        const db = new IDBFactory(), store = createNativeMediaInbox(db), item = await offer();
        await store.begin('bob', item.event); await part(store, item, 1);
        const reopened = createNativeMediaInbox(db);
        expect(await reopened.missing('bob', item.event.from_webid, 'file', item.event.manifest_digest)).toEqual([0]);
        await part(reopened, item, 0);
        expect((await reopened.assemble('bob', item.event.from_webid, 'file', item.event.manifest_digest)).bytes).toEqual(item.bytes);
        await reopened.release('bob', item.event.from_webid, 'file', item.event.manifest_digest, item.receiveId);
        expect(await reopened.list('bob')).toEqual([]);
    });
    it('rejects changed manifest/parts, foreign scopes and incorrect full receipts without replacing saved bytes', async () => {
        const store = createNativeMediaInbox(new IDBFactory()), item = await offer();
        await store.begin('bob', item.event); await part(store, item, 0); await part(store, item, 0);
        await expect(part(store, item, 0, new Uint8Array(NATIVE_CHUNK_BYTES).fill(66))).rejects.toThrow('Conflicting');
        await expect(store.put('other', item.event.from_webid, 'file', item.event.manifest_digest, 1, item.bytes.subarray(NATIVE_CHUNK_BYTES), await hash(item.bytes.subarray(NATIVE_CHUNK_BYTES)))).rejects.toThrow('offer');
        await expect(store.begin('bob', { ...item.event, cipher_digest: 'b'.repeat(64) })).rejects.toThrow('manifest');
        await expect(store.release('bob', item.event.from_webid, 'file', item.event.manifest_digest, 'b'.repeat(64))).rejects.toThrow('receipt');
        expect((await store.list('bob'))[0].received).toBe(1);
    });
    it('never assembles missing or corrupt ciphertext as a completed file', async () => {
        const store = createNativeMediaInbox(new IDBFactory()), item = await offer(); await store.begin('bob', item.event);
        await expect(store.assemble('bob', item.event.from_webid, 'file', item.event.manifest_digest)).rejects.toThrow('incomplete');
        await part(store, item, 0, new Uint8Array(NATIVE_CHUNK_BYTES).fill(66)); await part(store, item, 1);
        await expect(store.assemble('bob', item.event.from_webid, 'file', item.event.manifest_digest)).rejects.toThrow('digest');
        expect((await store.list('bob'))[0].received).toBe(2);
    });
    it('reserves bounded complete bytes and refuses new offers without evicting accepted work', async () => {
        const store = createNativeMediaInbox(new IDBFactory()), first = await offer(25 * 1024 * 1024);
        await store.begin('bob', first.event);
        const second = await offer(8 * 1024 * 1024, 'over');
        await expect(store.begin('bob', second.event)).rejects.toThrow('full');
        expect((await store.list('bob'))[0].manifestDigest).toBe(first.event.manifest_digest);
        expect(await store.list('other')).toEqual([]);
    });
    it('does not acknowledge a part when the context changes during its commit', async () => {
        const store = createNativeMediaInbox(new IDBFactory()), item = await offer(); await store.begin('bob', item.event);
        let active = true;
        const current = vi.fn(() => active);
        active = false;
        await expect(part(store, item, 0, undefined, current)).rejects.toThrow('digest');
        expect((await store.list('bob'))[0].received).toBe(0);
    });
    it('binds cleanup to the exact file receipt even when an earlier inline path already completed it', async () => {
        const store = createNativeMediaInbox(new IDBFactory()), item = await offer(); await store.begin('bob', item.event);
        await store.release('bob', item.event.from_webid, 'file', item.event.manifest_digest, item.receiveId);
        expect(await store.list('bob')).toEqual([]);
    });
});
