import { describe, it, expect, vi } from 'vitest';
import { createDmAttachments, prepareDmAttachment, parseDmAttachment, dmAssetContainer, dmAssetAcl,
    DM_ASSET_CHUNK, DM_ASSET_MAX, DM_ASSET_PREFIX } from './dm-attachments.js';
const owner = 'https://pod.example/alice/profile/card#me';
const container = dmAssetContainer(owner);
const meta = { name: 'private.txt', mime: 'text/plain' };
function fixture() {
    const resources = new Map(); let active = true;
    const fetch = vi.fn(async (url, opts = {}) => {
        if (opts.method === 'HEAD') return new Response(null, { status: resources.has(url) ? 200 : 404 });
        if (opts.method === 'PUT') {
            if (opts.headers['If-None-Match'] === '*' && resources.has(url)) return new Response(null, { status: 412 });
            resources.set(url, opts.body);
            return new Response(null, { status: 201 });
        }
        return resources.has(url) ? new Response(resources.get(url)) : new Response(null, { status: 404 });
    });
    return { resources, fetch, io: createDmAttachments({ fetch, current: () => active }), stop: () => { active = false; } };
}
const serialize = manifest => DM_ASSET_PREFIX + JSON.stringify(manifest);
describe('encrypted pod DM attachment storage', () => {
    it('stores only ciphertext and random chunk identity; keeps key/name/type in the private manifest', async () => {
        const h = fixture(), bytes = new Uint8Array(DM_ASSET_CHUNK + 123).fill(65);
        const prepared = await prepareDmAttachment(new Blob([bytes]), meta);
        const result = await h.io.upload(owner, prepared);
        expect(result.ok).toBe(true); expect(result.manifest.chunks).toBe(2);
        for (const [url, body] of h.resources) {
            if (!url.endsWith('.json')) continue;
            const chunk = JSON.parse(body);
            expect(Object.keys(chunk).sort()).toEqual(['data', 'id', 'index', 'v']);
            expect(body).not.toContain(meta.name); expect(body).not.toContain(meta.mime);
            expect(body).not.toContain(result.manifest.key); expect(body).not.toContain(result.manifest.iv);
        }
        expect((await h.io.download(owner, result.content)).bytes).toEqual(bytes);
        expect(h.fetch.mock.calls.every(([, opts]) => opts.redirect === 'error')).toBe(true);
        expect(h.fetch.mock.calls.filter(([url, opts]) => url.endsWith('.json') && opts.method === 'PUT')
            .every(([, opts]) => opts.headers['If-None-Match'] === '*')).toBe(true);
    });

    it('retains ciphertext and resource identity across a persisted draft retry, including a lost write response', async () => {
        const h = fixture(), prepared = await prepareDmAttachment(new Blob(['hello']), meta);
        const original = h.fetch.getMockImplementation(); let loseAck = true;
        h.fetch.mockImplementation(async (url, opts) => {
            const response = await original(url, opts);
            if (loseAck && url.endsWith('.json') && opts.method === 'PUT') { loseAck = false; throw Error('response lost'); }
            return response;
        });
        expect((await h.io.upload(owner, prepared)).ok).toBe(false);
        const saved = structuredClone(prepared), before = [...h.resources].filter(([url]) => url.endsWith('.json'));
        const restarted = createDmAttachments({ fetch: h.fetch });
        expect((await restarted.upload(owner, saved)).ok).toBe(true);
        expect([...h.resources].filter(([url]) => url.endsWith('.json'))).toEqual(before);
        expect((await restarted.download(owner, saved.content)).bytes).toEqual(new TextEncoder().encode('hello'));
    });

    it('never overwrites a conflicting existing resource or trusts unverified readback', async () => {
        const h = fixture(), prepared = await prepareDmAttachment(new Blob(['hello']), meta);
        const manifest = parseDmAttachment(prepared.content), url = `${container}asset-${manifest.id}-0.json`;
        h.resources.set(url, JSON.stringify({ v: 1, id: manifest.id, index: 0, data: 'eA==' }));
        const previous = h.resources.get(url);
        expect(await h.io.upload(owner, prepared)).toMatchObject({ ok: false, reason: 'readback_failed' });
        expect(h.resources.get(url)).toBe(previous);
    });

    it('rejects modified ciphertext, misplaced chunks, and incorrect encryption keys', async () => {
        const h = fixture(), prepared = await prepareDmAttachment(new Blob(['hello']), meta);
        const uploaded = await h.io.upload(owner, prepared), manifest = uploaded.manifest;
        const url = `${container}asset-${manifest.id}-0.json`, original = h.resources.get(url), chunk = JSON.parse(original);
        chunk.data = chunk.data[0] === 'A' ? 'B' + chunk.data.slice(1) : 'A' + chunk.data.slice(1);
        h.resources.set(url, JSON.stringify(chunk));
        expect((await h.io.download(owner, uploaded.content)).ok).toBe(false);
        h.resources.set(url, JSON.stringify({ ...JSON.parse(original), index: 1 }));
        expect((await h.io.download(owner, uploaded.content)).ok).toBe(false);
        h.resources.set(url, original);
        expect((await h.io.download(owner, serialize({ ...manifest, key: 'A'.repeat(43) }))).ok).toBe(false);
        h.fetch.mockClear();
        expect((await h.io.upload(owner, { ...prepared, content: serialize({ ...manifest, key: 'A'.repeat(43) }) })).ok).toBe(false);
        expect(h.fetch).not.toHaveBeenCalled();
    });

    it('derives all download paths from the sender and refuses URL-bearing manifests before fetching', async () => {
        const h = fixture(), prepared = await prepareDmAttachment(new Blob(['hello']), meta);
        const manifest = parseDmAttachment(prepared.content);
        for (const changed of [{ chunks: ['https://other.example/secret'] }, { url: 'https://other.example/' }, { id: '../escape' }, { chunks: 0 }]) {
            expect((await h.io.download(owner, serialize({ ...manifest, ...changed }))).ok).toBe(false);
        }
        for (const sender of ['https://user:password@pod.example/profile/card#me', 'https://pod.example/../profile/card#me', 'https://pod.example/a%2fb/profile/card#me', 'http://external.example/profile/card#me', 'room1']) {
            expect(dmAssetContainer(sender)).toBe(null);
            expect((await h.io.download(sender, prepared.content)).ok).toBe(false);
        }
        expect(h.fetch).not.toHaveBeenCalled();
        await h.io.download('https://other.example/bob/profile/card#me', prepared.content);
        expect(h.fetch.mock.calls[0][0]).toMatch(/^https:\/\/other.example\/bob\/proxion\/dm-assets\/asset-/);
    });

    it('validates size, metadata, executable signatures and voice limits before upload', async () => {
        for (const metadata of [{ ...meta, name: '../secret.txt' }, { ...meta, mime: 'text/html' }, { ...meta, kind: 'voice', mime: 'audio/webm', durationMs: 100 }, { ...meta, kind: 'voice', durationMs: 1000 }]) {
            expect((await prepareDmAttachment(new Blob(['hello']), metadata)).ok).toBe(false);
        }
        expect((await prepareDmAttachment(new Blob(['#!run']), meta)).ok).toBe(false);
        expect((await prepareDmAttachment(new Blob([new Uint8Array(DM_ASSET_MAX + 1)]), meta)).ok).toBe(false);
        expect((await prepareDmAttachment(new Blob(), { name: 'voice.webm', mime: 'audio/webm', kind: 'voice', durationMs: 1000 })).ok).toBe(false);
        const h = fixture(), empty = await prepareDmAttachment(new Blob(), meta);
        expect((await h.io.upload(owner, empty)).ok).toBe(true);
        expect((await h.io.download(owner, empty.content)).bytes).toHaveLength(0);
        const voice = await prepareDmAttachment(new Blob(['recorded']), { name: 'voice.webm', mime: 'audio/webm', kind: 'voice', durationMs: 1000 });
        expect(parseDmAttachment(voice.content)).toMatchObject({ kind: 'voice', durationMs: 1000 });
    });

    it('refuses private peer hosts before fetching unless they share our own pod origin', async () => {
        const prepared = await prepareDmAttachment(new Blob(['hello']), meta), fetch = vi.fn(async () => new Response(null, { status: 404 }));
        const reader = createDmAttachments({ fetch, getSelfWebId: () => owner });
        for (const sender of ['https://169.254.169.254/profile/card#me', 'https://10.0.0.1/profile/card#me', 'http://localhost:3000/profile/card#me']) {
            expect(await reader.download(sender, prepared.content)).toMatchObject({ ok: false, reason: 'unsafe_peer' });
        }
        expect(fetch).not.toHaveBeenCalled();
        const local = createDmAttachments({ fetch, getSelfWebId: () => 'http://localhost:3000/alice/profile/card#me' });
        await local.download('http://localhost:3000/bob/profile/card#me', prepared.content);
        expect(fetch).toHaveBeenCalledTimes(1);
    });

    it('uses a fresh file key and nonce for each preparation', async () => {
        const blob = new Blob(['hello']);
        const a = await prepareDmAttachment(blob, meta), b = await prepareDmAttachment(blob, meta);
        expect(a.content).not.toBe(b.content);
        expect(parseDmAttachment(a.content).key).not.toBe(parseDmAttachment(b.content).key);
        expect(parseDmAttachment(a.content).iv).not.toBe(parseDmAttachment(b.content).iv);
    });

    it('stops on a context change and never publishes a completed manifest', async () => {
        const h = fixture(), prepared = await prepareDmAttachment(new Blob([new Uint8Array(DM_ASSET_CHUNK + 10)]), meta);
        const progress = vi.fn(() => h.stop());
        expect(await h.io.upload(owner, prepared, progress)).toMatchObject({ ok: false, reason: 'context_changed' });
        expect(progress).toHaveBeenCalledTimes(1);
        expect([...h.resources.keys()].filter(url => url.endsWith('.json'))).toHaveLength(1);
        h.fetch.mockClear();
        expect((await h.io.download(owner, prepared.content)).ok).toBe(false);
        expect(h.fetch).not.toHaveBeenCalled();
    });

    it('refuses failed ACL writes and foreign ACL links before writing chunks', async () => {
        const prepared = await prepareDmAttachment(new Blob(['hello']), meta);
        for (const foreign of [false, true]) {
            const h = fixture(), original = h.fetch.getMockImplementation();
            h.resources.set(container, '');
            h.fetch.mockImplementation((url, opts) => {
                if (opts.method === 'HEAD' && foreign) return new Response(null, { headers: { Link: '<https://evil.example/.acl>; rel="acl"' } });
                if (url.endsWith('.acl')) return new Response(null, { status: 403 });
                return original(url, opts);
            });
            expect((await h.io.upload(owner, prepared)).ok).toBe(false);
            expect(h.fetch.mock.calls.some(([url]) => url.endsWith('.json'))).toBe(false);
            expect(h.fetch.mock.calls.some(([url]) => url.includes('evil.example'))).toBe(false);
        }
        expect(dmAssetAcl(container, owner, 'acp')).toContain('acp:memberAccessControl <#owner-ac>, <#public-ac>');
        expect(dmAssetAcl(container, owner)).toContain('acl:mode acl:Read.');
        expect(() => dmAssetAcl(container, 'https://other.example/profile/card#me')).toThrow();
    });

    it('rejects oversized streams and unexpected plaintext metadata in chunk responses', async () => {
        const prepared = await prepareDmAttachment(new Blob(['hello']), meta), manifest = parseDmAttachment(prepared.content);
        const cancel = vi.fn(), stream = new ReadableStream({ pull(controller) { controller.enqueue(new Uint8Array(49000)); }, cancel });
        const fetch = vi.fn(async () => new Response(stream));
        expect((await createDmAttachments({ fetch }).download(owner, prepared.content)).ok).toBe(false);
        expect(cancel).toHaveBeenCalledTimes(1);
        fetch.mockResolvedValue(new Response(JSON.stringify({ v: 1, id: manifest.id, index: 0, data: 'eA==', name: 'leaked.txt' })));
        expect((await createDmAttachments({ fetch }).download(owner, prepared.content)).ok).toBe(false);
    });
});
