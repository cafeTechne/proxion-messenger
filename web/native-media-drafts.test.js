import { describe, it, expect, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { createNativeMediaPreparation, createNativeMediaStore, NATIVE_INLINE_BYTES } from './native-media-drafts.js';
import { createSecondaryCrypto, decryptFile } from './e2e-secondary.js';
const view = { type: 'dm', id: 'certificate', peerWebid: 'did:key:bob' };
const metadata = { name: 'private.txt', mime: 'text/plain' };
function fixture() {
    let scope = 'alice/device', generation = 0, counter = 0;
    const db = new IDBFactory(), store = createNativeMediaStore(db), results = new Map();
    const encrypt = vi.fn(async (peer, text, options) => {
        if (!options.current()) throw Error('stale context');
        if (!results.has(options.sendId)) results.set(options.sendId, { ciphertext: 'cipher-' + counter++, nonce: 'nonce', msgNum: 0, pn: 0, ratchetPub: 'ratchet' });
        return results.get(options.sendId);
    });
    const secondary = createSecondaryCrypto({ encrypt, decrypt: vi.fn(), isKeyed: () => true, isSelf: () => false });
    const options = { store, secondary, getScope: () => scope, getGeneration: () => generation };
    return { db, store, secondary, encrypt, results, options, engine: () => createNativeMediaPreparation(options),
        scope: value => { scope = value; generation++; }, generation: () => { generation++; } };
}
describe('native media preparation recovery', () => {
    it('stores the source and file encryption before ratchet sealing and reopens the exact prepared result', async () => {
        const h = fixture(), engine = h.engine();
        await engine.enqueue(new Blob(['private bytes']), view, metadata, 'draft'); expect(h.encrypt).not.toHaveBeenCalled();
        const sealed = await engine.prepare('draft');
        expect(sealed.stage).toBe('sealed'); expect(await sealed.blob.text()).toBe('private bytes');
        expect(h.encrypt).toHaveBeenCalledTimes(1);
        const reopened = await h.engine().prepare('draft');
        expect(reopened.hdr).toEqual(sealed.hdr); expect(reopened.prepared).toEqual(sealed.prepared);
        expect(h.encrypt).toHaveBeenCalledTimes(1);
        const bytes = await decryptFile(reopened.prepared.ct, reopened.prepared.body.key, reopened.prepared.body.iv);
        expect(new TextDecoder().decode(bytes)).toBe('private bytes');
    });
    it('spends no ratchet key if file preparation cannot be committed', async () => {
        const h = fixture(), engine = h.engine(); await engine.enqueue(new Blob(['source']), view, metadata, 'draft');
        const transition = h.store.transition;
        h.store.transition = vi.fn(async (...args) => { if (args[3] === 'prepared') throw Error('quota'); return transition(...args); });
        await expect(engine.prepare('draft')).rejects.toThrow('quota'); expect(h.encrypt).not.toHaveBeenCalled();
        expect((await h.store.get('alice/device', 'draft')).stage).toBe('source');
        h.store.transition = transition; expect((await engine.prepare('draft')).stage).toBe('sealed');
    });
    it('reuses the ratchet journal after a crash between header sealing and the draft commit', async () => {
        const h = fixture(), engine = h.engine(); await engine.enqueue(new Blob(['source']), view, metadata, 'draft');
        const transition = h.store.transition;
        h.store.transition = vi.fn(async (...args) => { if (args[3] === 'sealed') throw Error('interrupted header commit'); return transition(...args); });
        await expect(engine.prepare('draft')).rejects.toThrow('interrupted header');
        const before = await h.store.get('alice/device', 'draft'); expect(before.stage).toBe('prepared');
        h.store.transition = transition; const after = await h.engine().prepare('draft');
        expect(after.prepared).toEqual(before.prepared); expect(h.results.size).toBe(1);
        expect(h.encrypt.mock.calls[1][2].sendId).toBe(h.encrypt.mock.calls[0][2].sendId);
    });
    it('rejects stale revisions and duplicate source ids without replacing file keys', async () => {
        const h = fixture(), engine = h.engine(); await engine.enqueue(new Blob(['original']), view, metadata, 'draft');
        await expect(engine.enqueue(new Blob(['replacement']), view, metadata, 'draft')).rejects.toThrow('already exists');
        const sealed = await engine.prepare('draft');
        await expect(h.store.transition('alice/device', 'draft', 1, 'prepared', sealed.prepared)).rejects.toThrow('draft changed');
        expect((await h.store.get('alice/device', 'draft')).prepared.body.key).toBe(sealed.prepared.body.key);
    });
    it('atomically caps concurrent admission and isolates scopes', async () => {
        const h = fixture(), engine = h.engine();
        const results = await Promise.allSettled(Array.from({ length: 12 }, (_, index) => engine.enqueue(new Blob(['source']), view, metadata, 'id-' + index)));
        expect(results.filter(result => result.status === 'fulfilled')).toHaveLength(10);
        h.scope('bob/device'); await engine.enqueue(new Blob(['bob source']), view, metadata, 'id-0');
        expect((await engine.list()).map(row => row.scope)).toEqual(['bob/device']);
        expect(await (await h.store.get('alice/device', 'id-0')).blob.text()).toBe('source');
    });
    it('does not let delayed old-account encryption block or commit into a new account', async () => {
        const h = fixture(), engine = h.engine(); await engine.enqueue(new Blob(['alice']), view, metadata, 'draft');
        let release, started;
        const waiting = new Promise(resolve => { release = resolve; }), entered = new Promise(resolve => { started = resolve; });
        const encrypt = crypto.subtle.encrypt.bind(crypto.subtle);
        const spy = vi.spyOn(crypto.subtle, 'encrypt').mockImplementationOnce(async (...args) => { started(); await waiting; return encrypt(...args); });
        try {
            const pending = engine.prepare('draft').catch(error => error); await entered;
            h.scope('bob/device'); await engine.enqueue(new Blob(['bob']), view, metadata, 'draft');
            expect((await engine.prepare('draft')).stage).toBe('sealed');
            release(); expect(await pending).toBeInstanceOf(Error);
            expect((await h.store.get('alice/device', 'draft')).stage).toBe('source');
            expect(h.encrypt).toHaveBeenCalledTimes(1);
        } finally { release(); spy.mockRestore(); }
    });
    it('refuses a restored draft whose peer mapping changed before sealing', async () => {
        const h = fixture(), engine = h.engine();
        await engine.enqueue(new Blob(['source']), view, metadata, 'draft');
        vi.spyOn(h.secondary, 'peerFor').mockReturnValue('did:key:replacement');
        await expect(engine.prepare('draft')).rejects.toThrow('destination changed');
        expect(h.encrypt).not.toHaveBeenCalled();
        expect((await h.store.get('alice/device', 'draft')).stage).toBe('source');
    });
    it('serializes simultaneous preparations of one draft without advancing twice', async () => {
        const h = fixture(), engine = h.engine();
        await engine.enqueue(new Blob(['source']), view, metadata, 'draft');
        const rows = await Promise.all([engine.prepare('draft'), engine.prepare('draft')]);
        expect(rows[0]).toEqual(rows[1]); expect(h.encrypt).toHaveBeenCalledTimes(1);
    });
    it('rejects oversized/empty files and pod destinations before encryption', async () => {
        const h = fixture(), engine = h.engine();
        await expect(engine.enqueue(new Blob([new Uint8Array(NATIVE_INLINE_BYTES)]), view, metadata)).rejects.toThrow('Invalid native');
        await expect(engine.enqueue(new Blob([]), view, metadata)).rejects.toThrow('Invalid native');
        await expect(engine.enqueue(new Blob(['x']), { ...view, peerWebid: 'https://bob.example/#me' }, metadata)).rejects.toThrow('Unsupported native');
        expect(await engine.list()).toEqual([]); expect(h.encrypt).not.toHaveBeenCalled();
    });
});
