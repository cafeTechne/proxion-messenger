import { describe, it, expect, vi } from 'vitest';
import { createAssetInventory, inventoryRoot, INVENTORY_PAGE } from './asset-inventory.js';
const root = 'https://pod.example/alice/', base = root + 'proxion/rooms/';
const directory = (url, children) => JSON.stringify({ '@id': url, '@type': ['http://www.w3.org/ns/ldp#BasicContainer'],
    'http://www.w3.org/ns/ldp#contains': children.map(value => ({ '@id': value })) });
function fixture() {
    let active = true;
    const resources = new Map();
    const fetch = vi.fn(async (url, options) => {
        if (options.method === 'HEAD') return new Response(null, { headers: { 'Content-Length': '123' } });
        return resources.has(url) ? new Response(resources.get(url)) : new Response(null, { status: 404 });
    });
    return { resources, fetch, io: createAssetInventory({ root, fetch, current: () => active }), stop: () => { active = false; } };
}
describe('own-pod room and legacy file inventory', () => {
    it('browses only scoped protocol paths and reads file metadata without downloading bytes', async () => {
        const h = fixture();
        h.resources.set(base, directory(base, ['room/', 'https://evil.example/room/', '../escape/', 'bad%2fid/']));
        h.resources.set(base + 'room/', directory(base + 'room/', ['asset-upload-0.json', 'files/', 'index.ttl', '2026/', '.acl', '../asset-x-0.json']));
        h.resources.set(base + 'room/files/', directory(base + 'room/files/', ['note.webm', 'message/', 'bad.pdf', '.acl']));
        h.resources.set(base + 'room/files/message/', directory(base + 'room/files/message/', ['old.txt', '.hidden', '..', 'secret.acl', '../escape.txt', 'https://evil.example/data']));
        expect((await h.io.rooms()).rooms).toEqual(['room']);
        const top = await h.io.browse('room', { references: [base + 'room/asset-upload-0.json'] });
        expect(top.rows.map(row => row.kind)).toEqual(['chunk', 'folder']);
        expect(top.rows[0]).toMatchObject({ referenced: true, size: 123 });
        const copies = await h.io.browse('room', { path: 'files/' });
        expect(copies.rows.map(row => row.kind).sort()).toEqual(['folder', 'voice']);
        const leaf = await h.io.browse('room', { path: 'files/message/' });
        expect(leaf.rows.map(row => row.name).sort()).toEqual(['.hidden', 'old.txt']);
        expect(leaf.rows.every(row => row.referenced === false)).toBe(true);
        expect(h.fetch.mock.calls.every(([url, options]) => url.startsWith(base) && ['HEAD', undefined].includes(options.method) && options.redirect === 'error')).toBe(true);
        expect(h.fetch.mock.calls.filter(([url]) => url.endsWith('.txt')).every(([, options]) => options.method === 'HEAD')).toBe(true);
    });
    it('paginates metadata reads and marks server pagination as incomplete', async () => {
        const h = fixture(), container = base + 'room/';
        h.resources.set(container, directory(container, Array.from({ length: 55 }, (_, index) => `asset-${index}-0.json`)));
        const original = h.fetch.getMockImplementation();
        h.fetch.mockImplementation(async (url, options) => {
            const response = await original(url, options);
            if (!options.method) response.headers.set('Link', '<next>; rel="next"');
            return response;
        });
        const first = await h.io.browse('room');
        expect(first.rows).toHaveLength(INVENTORY_PAGE); expect(first.next).toBe(INVENTORY_PAGE); expect(first.partial).toBe(true);
        expect(h.fetch).toHaveBeenCalledTimes(1 + INVENTORY_PAGE);
        const second = await h.io.browse('room', { offset: INVENTORY_PAGE });
        expect(second.rows).toHaveLength(5); expect(second.next).toBe(null);
    });
    it('does not confuse failed or malformed listings with empty storage', async () => {
        const h = fixture();
        expect(await h.io.rooms()).toMatchObject({ ok: true, rooms: [] });
        for (const response of [new Response(null, { status: 403 }), new Response('{}'),
            new Response(directory('https://evil.example/', [])), new Response('not JSON')]) {
            h.fetch.mockResolvedValueOnce(response);
            expect((await h.io.rooms()).ok).toBe(false);
        }
    });
    it('stops metadata requests after authorization, rate-limit or server failures and retains unknown rows', async () => {
        for (const status of [401, 403, 429, 503]) {
            const h = fixture();
            h.fetch.mockResolvedValueOnce(new Response(directory(base + 'room/', ['asset-a-0.json', 'asset-b-0.json'])))
                .mockResolvedValueOnce(new Response(null, { status }));
            const result = await h.io.browse('room');
            expect(result.partial).toBe(true);
            expect(result.rows).toHaveLength(2);
            expect(result.rows.every(row => !row.available && !row.referenced && row.size === null)).toBe(true);
            expect(h.fetch).toHaveBeenCalledTimes(2);
        }
    });
    it('caps actual streamed bytes and stops reads after the account/view changes', async () => {
        const h = fixture(), cancel = vi.fn();
        const stream = new ReadableStream({ pull(controller) { controller.enqueue(new Uint8Array(530000)); }, cancel });
        h.fetch.mockResolvedValueOnce(new Response(stream));
        expect((await h.io.rooms()).ok).toBe(false); expect(cancel).toHaveBeenCalledTimes(1);
        h.resources.set(base + 'room/', directory(base + 'room/', ['asset-a-0.json', 'asset-b-0.json']));
        const original = h.fetch.getMockImplementation();
        h.fetch.mockImplementation(async (url, options) => { const response = await original(url, options); if (options.method === 'HEAD') h.stop(); return response; });
        const before = h.fetch.mock.calls.length;
        expect(await h.io.browse('room')).toMatchObject({ ok: false, reason: 'context_changed' });
        expect(h.fetch.mock.calls.length - before).toBe(2);
    });
    it('refuses path injection before fetching and reports unknown metadata without false zero sizes', async () => {
        const h = fixture();
        for (const path of ['../', 'files/../', 'files/a/deep/', 'files/%2e%2e/', '.acl']) {
            expect((await h.io.browse('room', { path })).ok).toBe(false);
        }
        expect((await h.io.browse('../room')).ok).toBe(false);
        expect(h.fetch).not.toHaveBeenCalled();
        h.resources.set(base + 'room/', directory(base + 'room/', ['asset-a-0.json']));
        h.fetch.mockResolvedValueOnce(new Response(h.resources.get(base + 'room/'))).mockResolvedValueOnce(new Response(null));
        expect((await h.io.browse('room')).rows[0].size).toBe(null);
        for (const bad of ['http://external.example/', 'https://user@pod.example/', root + '?x=1', 'https://pod.example/a%2fb/', 'https://pod.example/../alice/']) expect(inventoryRoot(bad)).toBe(null);
    });
});
