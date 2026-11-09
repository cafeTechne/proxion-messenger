import { describe, it, expect, vi } from 'vitest';
import { createRoomAttachments, parseRoomAttachment, ROOM_ASSET_PREFIX, ROOM_ASSET_CHUNK } from './room-attachments.js';
const root = 'https://pod.example/proxion/rooms/room1/';
function fixture() {
    const resources = new Map(); let active = true, reject = false, reads = 0;
    const fetch = vi.fn(async (url, options = {}) => {
        if (options.method === 'POST') {
            if (reject) return new Response('', { status: 403 });
            const location = root + options.headers.Slug;
            resources.set(location, options.body);
            return new Response('', { status: 201, headers: { Location: location } });
        }
        reads++;
        return resources.has(url) ? new Response(resources.get(url), { status: 200 }) : new Response('', { status: 404 });
    });
    const io = createRoomAttachments({ fetch, current: () => active });
    return { io, fetch, resources, stop: () => { active = false; }, reject: () => { reject = true; }, reads: () => reads };
}
const metadata = { id: 'upload-1', name: 'sample.txt', mime: 'text/plain' };
describe('room-owned immutable attachments', () => {
    it('uploads and reads back multiple chunks, then verifies exact download bytes', async () => {
        const h = fixture(), bytes = new Uint8Array(ROOM_ASSET_CHUNK * 2 + 7).map((_, index) => index % 255);
        const result = await h.io.upload(root, new Blob([bytes]), metadata);
        expect(result.ok).toBe(true); expect(result.manifest.chunks).toHaveLength(3);
        expect(h.reads()).toBe(3);
        expect((await h.io.download(root, result.content)).bytes).toEqual(bytes);
        expect(h.fetch.mock.calls.every(([, options]) => options.redirect === 'error')).toBe(true);
    });
    it('refuses rejected writes and corrupted readback without publishing a manifest', async () => {
        const h = fixture(); h.reject();
        expect(await h.io.upload(root, new Blob(['hi']), metadata)).toMatchObject({ ok: false });
        const other = fixture();
        other.fetch.mockResolvedValueOnce(new Response('', { status: 201, headers: { Location: root + 'asset-upload-1-0.json' } }));
        expect(await other.io.upload(root, new Blob(['hi']), metadata)).toMatchObject({ ok: false, reason: 'readback_failed' });
    });
    it('rejects modified bytes, swapped chunks, and a claimed foreign download before fetching', async () => {
        const h = fixture(), uploaded = await h.io.upload(root, new Blob(['hello']), metadata);
        const url = uploaded.manifest.chunks[0];
        const chunk = JSON.parse(h.resources.get(url)); chunk.data = 'd29ybGQ='; h.resources.set(url, JSON.stringify(chunk));
        expect(await h.io.download(root, uploaded.content)).toMatchObject({ ok: false, reason: 'integrity' });
        h.fetch.mockClear();
        const foreign = { ...uploaded.manifest, chunks: ['https://evil.example/asset-upload-1-0.json'] };
        expect(await h.io.download(root, ROOM_ASSET_PREFIX + JSON.stringify(foreign))).toMatchObject({ ok: false });
        expect(h.fetch).not.toHaveBeenCalled();
        expect(parseRoomAttachment(ROOM_ASSET_PREFIX + JSON.stringify({ ...foreign, chunks: [root + '../asset-upload-1-0.json'] }), root)).toBeNull();
    });
    it('reuses confirmed chunks on retry and stops when the context changes', async () => {
        const h = fixture(), uploaded = await h.io.upload(root, new Blob(['hello']), metadata);
        h.fetch.mockClear();
        expect((await h.io.upload(root, new Blob(['hello']), metadata, () => {}, uploaded.manifest.chunks)).ok).toBe(true);
        expect(h.fetch.mock.calls.some(([, options]) => options.method === 'POST')).toBe(false);
        h.stop(); h.fetch.mockClear();
        expect((await h.io.download(root, uploaded.content)).ok).toBe(false); expect(h.fetch).not.toHaveBeenCalled();
    });
    it('validates metadata before sending and accepts empty files', async () => {
        const h = fixture();
        expect((await h.io.upload(root, new Blob(['hi']), { ...metadata, mime: 'text/html' })).ok).toBe(false);
        expect(h.fetch).not.toHaveBeenCalled();
        const empty = await h.io.upload(root, new Blob(), metadata);
        expect(empty.ok).toBe(true); expect((await h.io.download(root, empty.content)).bytes.length).toBe(0);
    });
    it('cancels an oversized response stream before reading the whole resource', async () => {
        const h = fixture(), uploaded = await h.io.upload(root, new Blob(['hi']), metadata);
        const cancel = vi.fn();
        const stream = new ReadableStream({ pull(controller) { controller.enqueue(new Uint8Array(49000)); }, cancel });
        h.fetch.mockResolvedValueOnce(new Response(stream));
        expect((await h.io.download(root, uploaded.content)).ok).toBe(false);
        expect(cancel).toHaveBeenCalledOnce();
    });
});
