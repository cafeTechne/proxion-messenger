import { describe, it, expect, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { createDmMedia, createDmMediaStore, deliverDmMedia } from './dm-media.js';
import { parseDmAttachment } from './dm-attachments.js';

describe('createDmMediaStore', () => {
    it('preserves legacy drafts as unknown and prevents older writers reopening the upgraded database', async () => {
        const db = new IDBFactory();
        const legacy = await new Promise(resolve => {
            const req = db.open('proxion-dm-media-v1', 1);
            req.onupgradeneeded = () => req.result.createObjectStore('drafts', { keyPath: 'key' });
            req.onsuccess = () => resolve(req.result);
        });
        await new Promise(resolve => {
            const tx = legacy.transaction('drafts', 'readwrite');
            tx.objectStore('drafts').put({ key: 'legacy', actor: 'alice', deliveryState: 'never', blob: new Blob(['source']) });
            tx.oncomplete = resolve;
        }); legacy.close();
        const rows = await createDmMediaStore(db).list('alice');
        expect(rows[0].deliveryState).toBe('unknown');
        expect(await rows[0].blob.text()).toBe('source');
        const oldWriter = await new Promise(resolve => {
            const req = db.open('proxion-dm-media-v1', 1); req.onerror = () => resolve(req.error.name);
        });
        expect(oldWriter).toBe('VersionError');
    });
    it('atomically caps each account at ten drafts and retains prepared blobs across reopen', async () => {
        const db = new IDBFactory(), store = createDmMediaStore(db);
        const results = await Promise.allSettled(Array.from({ length: 12 }, (_, i) => store.put({ key: 'a' + i, actor: 'alice', prepared: new Blob(['cipher']) })));
        expect(results.filter(result => result.status === 'fulfilled')).toHaveLength(10);
        await store.put({ key: 'bob', actor: 'bob' });
        const reopened = createDmMediaStore(db), rows = await reopened.list('alice');
        expect(rows).toHaveLength(10);
        expect(await rows[0].prepared.text()).toBe('cipher');
        expect(await reopened.list('bob')).toHaveLength(1);
        await reopened.put({ ...rows[0], name: 'changed' });
        await reopened.remove(rows[0].key);
        expect(await reopened.list('alice')).toHaveLength(9);
    });
});

function fixture({ postFails = false, uploadFails = false, partialFailure = false, deleteFails = false } = {}) {
    const rows = new Map(), resources = new Map(), calls = [], onMessage = vi.fn(), showToast = vi.fn();
    let active = true, handler;
    const panel = { hidden: true, innerHTML: '', replaceChildren() { this.innerHTML = ''; }, addEventListener(_, fn) { handler = fn; } };
    const ctx = {
        actor: 'https://pod.example/alice/profile/card#me',
        peerWebId: 'https://pod.example/bob/profile/card#me',
        threadId: 'https://pod.example/bob/profile/card#me',
        senderName: 'Alice',
        current: () => active,
        fetch: async (url, options = {}) => {
            calls.push({ method: options.method || 'GET', url });
            if (uploadFails && options.method === 'PUT' && url.includes('asset-') && (!partialFailure || !url.endsWith('-0.json'))) {
                return new Response('fail', { status: 500 });
            }
            if (options.method === 'DELETE') {
                if (deleteFails) return new Response(null, { status: 503 });
                resources.delete(url); return new Response(null, { status: 204 });
            }
            if (options.method === 'HEAD') {
                return new Response('', { status: 200, headers: { Link: '<.acl>; rel="acl"' } });
            }
            if (options.method === 'PUT') {
                resources.set(url, options.body);
                return new Response('', { status: 201 });
            }
            if (options.method === 'GET' || !options.method) {
                const body = resources.get(url);
                return body ? new Response(body, { status: 200, headers: { 'Content-Type': 'application/json', ETag: '"chunk"' } }) : new Response('', { status: 404 });
            }
            return new Response('', { status: 200 });
        },
    };
    const store = {
        list: async actor => [...rows.values()].filter(row => row.actor === actor),
        put: async row => rows.set(row.key, structuredClone(row)),
        remove: async key => rows.delete(key),
    };
    const post = vi.fn(async row => ({ ok: !postFails, message: { content: row.content, message_id: row.id } }));
    const options = { getContext: () => ctx, post, store, onMessage, showToast, confirmCleanup: async () => true, doc: { getElementById: () => panel } };
    const controller = createDmMedia(options);
    return {
        controller, rows, calls, resources, options, panel, post, onMessage, showToast, ctx,
        offline: () => { active = false; },
        restorePost: () => { postFails = false; },
        restoreUpload: () => { uploadFails = false; },
        restoreCleanup: () => { deleteFails = false; },
        cleanup: key => handler({ target: { closest: selector => selector.includes('cleanup') ? { dataset: { dmMediaCleanup: key } } : null } }),
        retry: key => handler({ target: { closest: selector => selector.includes('retry') ? { dataset: { dmMediaRetry: key } } : null } }),
        cancel: key => handler({ target: { closest: selector => selector.includes('cancel') ? { dataset: { dmMediaCancel: key } } : null } }),
    };
}

describe('durable dm-media drafts', () => {
    it('keeps a late delivery acknowledgement in its original draft after navigation', async () => {
        const h = fixture();
        h.options.post = async row => {
            h.offline();
            return { ok: true, message: { message_id: row.id, content: row.content }, historySaved: false };
        };
        await createDmMedia(h.options).send(new Blob(['file']), { name: 'test.txt', mime: 'text/plain' });
        expect(h.rows.size).toBe(1);
        const row = [...h.rows.values()][0];
        expect(row.delivered.message_id).toBe(row.id);
        expect(row.actor).toBe('https://pod.example/alice/profile/card#me');
        expect(h.onMessage).not.toHaveBeenCalled();
    });
    it('retains an accepted send across a history failure without delivering it again', async () => {
        const h = fixture();
        let history = false;
        const send = vi.fn(async () => true);
        h.options.post = async row => {
            if (!row.delivered) await send();
            return { ok: true, message: { message_id: row.id, content: row.content }, historySaved: history };
        };
        createDmMedia(h.options);
        // Use the recreated controller so its post callback is the new one.
        await createDmMedia(h.options).send(new Blob(['file']), { name: 'test.txt', mime: 'text/plain' });
        const key = [...h.rows.keys()][0];
        expect(h.rows.get(key).delivered).toBeTruthy();
        history = true;
        await h.retry(key);
        expect(send).toHaveBeenCalledTimes(1);
        expect(h.rows.size).toBe(0);
    });
    it('persists prepared draft before upload and resumes after recreation without re-encrypting', async () => {
        const h = fixture({ postFails: true });
        await h.controller.send(new Blob(['hello private world']), { name: 'test.txt', mime: 'text/plain' });
        expect(h.rows.size).toBe(1);
        expect(h.onMessage).not.toHaveBeenCalled();

        const [key, draft] = [...h.rows.entries()][0];
        expect(draft.content).toContain('proxion-dm-attachment:v1');
        expect(draft.uploaded).toBe(true);
        const manifest1 = parseDmAttachment(draft.content);
        expect(manifest1).not.toBeNull();
        expect(manifest1.name).toBe('test.txt');

        const putCalls = h.calls.filter(c => c.method === 'PUT' && c.url.includes('asset-')).length;
        expect(putCalls).toBeGreaterThan(0);

        // Recreate controller, restore post, retry: should NOT re-upload since uploaded=true
        h.restorePost();
        createDmMedia(h.options);
        await h.retry(key);

        expect(h.rows.size).toBe(0);
        expect(h.onMessage).toHaveBeenCalledOnce();
        const postCall = h.post.mock.calls[1][0];
        expect(postCall.id).toBe(draft.id);
        expect(postCall.content).toBe(draft.content);
        const putCallsAfter = h.calls.filter(c => c.method === 'PUT' && c.url.includes('asset-')).length;
        expect(putCallsAfter).toBe(putCalls); // zero additional uploads
    });

    it('keeps draft when upload fails and allows retry with same key', async () => {
        const h = fixture({ uploadFails: true });
        await h.controller.send(new Blob(['hello private world']), { name: 'test.txt', mime: 'text/plain' });
        expect(h.rows.size).toBe(1);
        const [key, draft] = [...h.rows.entries()][0];
        expect(draft.uploaded).toBe(false);
        expect(h.showToast).toHaveBeenCalledWith('file.sendFailed', 'error');

        // Retry upload after network restores
        h.restoreUpload();
        await h.retry(key);
        expect(h.rows.size).toBe(0);
        expect(h.onMessage).toHaveBeenCalledOnce();
    });

    it('allows cancelling a pending draft', async () => {
        const h = fixture({ uploadFails: true });
        await h.controller.send(new Blob(['hello private world']), { name: 'test.txt', mime: 'text/plain' });
        expect(h.rows.size).toBe(1);
        const key = [...h.rows.keys()][0];

        await h.cancel(key);
        expect(h.rows.size).toBe(0);
        expect(h.panel.hidden).toBe(true);
    });

    it('does not send when durable draft storage fails', async () => {
        const h = fixture();
        h.options.store.put = async () => { throw Error('quota'); };
        await h.controller.send(new Blob(['file']), { name: 'test.txt', mime: 'text/plain' });
        expect(h.calls).toHaveLength(0);
        expect(h.post).not.toHaveBeenCalled();
        expect(h.showToast).toHaveBeenCalledWith('roomMedia.storageFailed', 'error');
    });

    it('does not retry another account or peer draft', async () => {
        const h = fixture({ postFails: true });
        await h.controller.send(new Blob(['file']), { name: 'test.txt', mime: 'text/plain' });
        const key = [...h.rows.keys()][0];
        h.ctx.peerWebId = 'https://pod.example/charlie/profile/card#me';
        await h.retry(key);
        expect(h.post).toHaveBeenCalledOnce(); // only initial attempt
        expect(h.rows.size).toBe(1);
    });

    it('refuses oversized files and empty voice recordings', async () => {
        const h = fixture();
        await h.controller.send(new Blob(['a'.repeat(2 * 1024 * 1024 + 1)]), { name: 'big.txt', mime: 'text/plain' });
        expect(h.showToast).toHaveBeenCalledWith('file.podDmTooLarge', 'error');
        expect(h.rows.size).toBe(0);

        await h.controller.send(new Blob([]), { name: 'voice.webm', mime: 'audio/webm', kind: 'voice' });
        expect(h.showToast).toHaveBeenCalledWith('voiceMsg.readFailed', 'error');
        expect(h.rows.size).toBe(0);
    });
});

describe('encrypted media delivery confirmation', () => {
    function fixture() {
        const draft = { id: 'media-id', content: 'private manifest' }, ctx = { actor: 'alice', peerWebId: 'bob', threadId: 'thread' };
        const deps = { current: () => true, obtainKey: vi.fn(async () => {}), hasKey: () => true,
            encrypt: vi.fn(async () => ({ ciphertext: 'opaque', nonce: 'n', msgNum: 1, pn: 0, ratchetPub: 'rp' })),
            cachePeerPub: vi.fn(), getPeerDevices: () => [], getOwnDevices: () => [], getDeviceId: () => 'self-device', getPub: () => 'pub',
            dropDm: vi.fn(async () => true), dropFanout: vi.fn(async () => false) };
        return { draft, ctx, deps };
    }
    it('waits for real acceptance and passes ciphertext only', async () => {
        const h = fixture(); let finish;
        h.deps.dropDm = vi.fn(() => new Promise(resolve => { finish = resolve; }));
        const delivery = deliverDmMedia(h.draft, h.ctx, h.deps);
        await vi.waitFor(() => expect(finish).toBeTypeOf('function'));
        expect(h.deps.dropDm.mock.calls[0][0]).toMatchObject({ e2e: true, content: 'opaque', message_id: 'media-id' });
        finish(false);
        expect(await delivery).toBe(false);
    });
    it('never sends plaintext when a key is missing or encryption fails', async () => {
        const h = fixture(); h.deps.hasKey = () => false;
        expect(await deliverDmMedia(h.draft, h.ctx, h.deps)).toBe(false);
        expect(h.deps.dropDm).not.toHaveBeenCalled();
        h.deps.hasKey = () => true;
        h.deps.encrypt.mockRejectedValue(new Error('encryption failed'));
        await expect(deliverDmMedia(h.draft, h.ctx, h.deps)).rejects.toThrow();
        expect(h.deps.dropDm).not.toHaveBeenCalled();
    });
    it('confirms recipient-device fanout without falling back to a different session', async () => {
        const h = fixture(); h.deps.getPeerDevices = () => [{ device_id: 'one', pub_b64u: 'p1' }, { device_id: 'two', pub_b64u: 'p2' }];
        expect(await deliverDmMedia(h.draft, h.ctx, h.deps)).toBe(false);
        expect(h.deps.dropDm).not.toHaveBeenCalled();
        expect(h.deps.dropFanout.mock.calls[0][0].fanout.map(entry => entry.to_device_id)).toEqual(['one', 'two']);
        expect(h.deps.encrypt.mock.calls.map(([pid]) => pid)).toEqual(['bob#one', 'bob#two']);
    });
    it('stops before sending if the original context changes during encryption', async () => {
        const h = fixture(); let current = true;
        h.deps.current = () => current;
        h.deps.encrypt.mockImplementation(async () => { current = false; return { ciphertext: 'opaque' }; });
        expect(await deliverDmMedia(h.draft, h.ctx, h.deps)).toBe(false);
        expect(h.deps.dropDm).not.toHaveBeenCalled();
    });
});

describe('unused DM upload cleanup eligibility and recovery', () => {
    it('removes a partial never-published upload after confirmation and leaves sent data alone', async () => {
        const h = fixture({ uploadFails: true, partialFailure: true });
        await h.controller.send(new Blob([new Uint8Array(33000)]), { name: 'partial.txt', mime: 'text/plain' });
        const [key, row] = [...h.rows][0];
        expect(row.deliveryState).toBe('never');
        expect([...h.resources.keys()].filter(url => url.endsWith('.json'))).toHaveLength(1);
        await h.controller.refresh();
        expect(h.panel.innerHTML).toContain('data-dm-media-cleanup');
        await h.cleanup(key);
        expect(h.rows.size).toBe(0);
        expect([...h.resources.keys()].filter(url => url.endsWith('.json'))).toHaveLength(0);
        expect(h.post).not.toHaveBeenCalled();
    });
    it('persists cleanup intent and resumes after failure/recreation without ever posting', async () => {
        const h = fixture({ uploadFails: true, partialFailure: true, deleteFails: true });
        await h.controller.send(new Blob([new Uint8Array(33000)]), { name: 'partial.txt', mime: 'text/plain' });
        const key = [...h.rows.keys()][0];
        await h.cleanup(key);
        expect(h.rows.get(key).cleanup).toBe(true);
        expect(h.rows.get(key).prepared).toBe(null);
        expect(h.rows.get(key).uploaded).toBe(false);
        expect(h.rows.get(key).content).toBe('');
        expect(h.rows.get(key).cleanupPrepared.ciphertext).toBeInstanceOf(Blob);
        await h.retry(key);
        expect(h.post).not.toHaveBeenCalled();
        h.restoreCleanup(); createDmMedia(h.options);
        await h.cleanup(key);
        expect(h.rows.size).toBe(0);
        expect(h.post).not.toHaveBeenCalled();
    });
    it('records attempted delivery before post and never offers cleanup for uncertain or legacy drafts', async () => {
        const h = fixture({ postFails: true });
        await h.controller.send(new Blob(['file']), { name: 'test.txt', mime: 'text/plain' });
        const [key, row] = [...h.rows][0];
        expect(h.post.mock.calls[0][0].deliveryState).toBe('attempted');
        expect(row.deliveryState).toBe('attempted');
        await h.controller.refresh();
        expect(h.panel.innerHTML).not.toContain('data-dm-media-cleanup');
        await h.cleanup(key);
        expect(h.calls.some(call => call.method === 'DELETE')).toBe(false);
        delete row.deliveryState; h.rows.set(key, row);
        await h.cleanup(key);
        expect(h.calls.some(call => call.method === 'DELETE')).toBe(false);
        await h.cancel(key);
        expect(h.rows.size).toBe(0);
        expect([...h.resources.keys()].some(url => url.endsWith('.json'))).toBe(true);
    });
    it('does no cleanup when confirmation is cancelled or the view changes', async () => {
        const h = fixture({ uploadFails: true, partialFailure: true });
        await h.controller.send(new Blob([new Uint8Array(33000)]), { name: 'partial.txt', mime: 'text/plain' });
        const key = [...h.rows.keys()][0];
        h.options.confirmCleanup = async () => false; createDmMedia(h.options);
        await h.cleanup(key);
        expect(h.rows.get(key).cleanup).not.toBe(true);
        h.options.confirmCleanup = async () => { h.offline(); return true; }; createDmMedia(h.options);
        await h.cleanup(key);
        expect(h.calls.some(call => call.method === 'DELETE')).toBe(false);
        expect(h.rows.size).toBe(1);
    });
    it('retains an ineligible draft when the delivery-state write fails before post', async () => {
        const h = fixture({ uploadFails: true });
        await h.controller.send(new Blob(['file']), { name: 'test.txt', mime: 'text/plain' });
        const key = [...h.rows.keys()][0]; h.restoreUpload();
        const original = h.options.store.put;
        h.options.store.put = async row => { if (row.deliveryState === 'attempted') throw Error('quota'); return original(row); };
        await h.retry(key);
        expect(h.post).not.toHaveBeenCalled();
        expect(h.rows.get(key).deliveryState).toBe('never');
    });
});
