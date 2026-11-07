import { describe, it, expect, vi } from 'vitest';
import { createPodRoomIo, validateWebId, buildWacAcl } from './podroomio.js';
import { buildRoomDescriptor } from './roomdesc.js';
import { CHAT_CLASS, NS } from './typeindex.js';

const ROOT = 'https://pod.example/alice/';
const ME = ROOT + 'profile/card#me';
const INDEX = ROOT + 'settings/publicTypeIndex.ttl';
const CONTAINER = ROOT + 'proxion/rooms/empty-room/';
const DESC = buildRoomDescriptor({ roomId: 'empty-room', title: 'Empty room', owner: ME, members: [{ webid: ME, role: 'owner' }] });

function response(status, json, etag = null) {
    return { ok: status >= 200 && status < 300, status, headers: { get: (key) => key.toLowerCase() === 'etag' ? etag : null }, text: async () => JSON.stringify(json) };
}

function harness(route) {
    const fetch = vi.fn(async (url, opts = {}) => route(url, opts) || response(404, null));
    const io = createPodRoomIo({ fetch, isLoggedIn: () => true, storageRoot: () => ROOT, webId: () => ME });
    return { fetch, io };
}

describe('owned room restoration', () => {
    it('restores an empty room after reload when the profile never linked its type index', async () => {
        const h = harness((url) => {
            if (url === ME) return response(200, [{ '@id': ME }]);
            if (url === INDEX) return response(200, [{
                [NS.solid + 'forClass']: [{ '@id': CHAT_CLASS }],
                [NS.solid + 'instanceContainer']: [{ '@id': CONTAINER }],
            }]);
            if (url === ROOT + 'rooms/empty-room/room.json') return response(200, DESC);
        });
        expect(await h.io.podListOwnedRoomDescriptors(ME)).toMatchObject([{ room_id: 'empty-room', owner: ME }]);
        expect(h.fetch.mock.calls.some(([url]) => url === INDEX)).toBe(true);
    });

    it('recovers persisted descriptors when chat registration failed, with safe own-pod paths only', async () => {
        const h = harness((url) => {
            if (url === ROOT + 'rooms/') return response(200, [{ '@id': url, 'http://www.w3.org/ns/ldp#contains': [
                { '@id': ROOT + 'rooms/empty-room/' },
                { '@id': ROOT + 'rooms/../elsewhere/' },
                { '@id': ROOT + 'rooms/nested/room/' },
                { '@id': 'https://attacker.example/rooms/foreign/' },
            ] }]);
            if (url === ROOT + 'rooms/empty-room/room.json') return response(200, DESC);
        });
        expect(await h.io.podListOwnedRoomDescriptors(ME)).toMatchObject([{ room_id: 'empty-room' }]);
        expect(h.fetch.mock.calls.filter(([url]) => url.endsWith('/room.json')).map(([url]) => url))
            .toEqual([ROOT + 'rooms/empty-room/room.json']);
    });

    it('does not restore descriptors owned by someone else', async () => {
        const h = harness((url) => {
            if (url === ROOT + 'rooms/') return response(200, { contains: [{ '@id': 'empty-room/' }] });
            if (url === ROOT + 'rooms/empty-room/room.json') return response(200, { ...DESC, owner: 'https://other.example/#me' });
        });
        expect(await h.io.podListOwnedRoomDescriptors(ME)).toEqual([]);
    });

    it('deregisters the fallback index so a deleted room cannot return on reload', async () => {
        const h = harness((url, opts) => url === INDEX && opts.method === 'PATCH' ? response(205, null) : null);
        expect(await h.io.podDeregisterChat(CONTAINER)).toBe(true);
        expect(h.fetch).toHaveBeenCalledWith(INDEX, expect.objectContaining({ method: 'PATCH', body: expect.stringContaining('DELETE DATA') }));
    });
});


describe('room ACL owner identities', () => {
    it('accepts HTTPS and loopback HTTP WebIDs used by local pods', () => {
        for (const owner of ['https://pod.example/profile/card#me', 'http://localhost:3000/alice/profile/card#me', 'http://127.0.0.1:3000/alice/#me', 'http://[::1]:3000/alice/#me']) {
            expect(validateWebId(owner)).toBe(true);
            expect(buildWacAcl(owner, [], 'http://localhost:3000/alice/rooms/r/')).toContain(`acl:agent <${owner}>`);
        }
    });
    it('rejects remote HTTP, non-web identifiers and Turtle injection', () => {
        for (const owner of ['http://pod.example/profile/card#me', 'did:key:zA', 'javascript:alert(1)', 'https://pod.example/> . <#evil>']) {
            expect(validateWebId(owner)).toBe(false);
            expect(() => buildWacAcl(owner, [], 'https://pod.example/rooms/r/')).toThrow();
        }
    });
});


describe('versioned owner descriptors', () => {
    const descriptorUrl = ROOT + 'rooms/empty-room/room.json';

    it('reads an uncached JSON version and uses exactly that strong ETag for its write', async () => {
        const h = harness((url, opts) => {
            if (url !== descriptorUrl) return null;
            return opts.method === 'PUT' ? response(205) : response(200, DESC, '"v1"');
        });
        const snapshot = await h.io.podReadRoomDescriptorVersion('empty-room');
        expect(snapshot).toMatchObject({ ok: true, etag: '"v1"', roomId: 'empty-room', url: descriptorUrl, missing: false });
        expect(await h.io.podWriteRoomDescriptorVersion(snapshot.descriptor, snapshot)).toMatchObject({ ok: true });
        expect(h.fetch).toHaveBeenNthCalledWith(1, descriptorUrl, expect.objectContaining({ cache: 'no-store', redirect: 'error' }));
        expect(h.fetch).toHaveBeenNthCalledWith(2, descriptorUrl, expect.objectContaining({ method: 'PUT',
            headers: { 'Content-Type': 'application/json', 'If-Match': '"v1"' }, redirect: 'error' }));
    });

    it('creates only against a confirmed missing resource and exposes precondition conflicts', async () => {
        const h = harness((url, opts) => url === descriptorUrl ? response(opts.method === 'PUT' ? 412 : 404) : null);
        const snapshot = await h.io.podReadRoomDescriptorVersion('empty-room');
        expect(snapshot).toMatchObject({ ok: true, missing: true, descriptor: null });
        expect(await h.io.podWriteRoomDescriptorVersion(DESC, snapshot)).toEqual({ ok: false, conflict: true, status: 412 });
        expect(h.fetch.mock.calls[1][1].headers['If-None-Match']).toBe('*');
    });

    it.each([null, 'W/"weak"', '"' + 'a'.repeat(513) + '"'])('refuses a conditional update with an unavailable/unsupported ETag', async (etag) => {
        const h = harness((url) => url === descriptorUrl ? response(200, DESC, etag) : null);
        const snapshot = await h.io.podReadRoomDescriptorVersion('empty-room');
        expect(await h.io.podWriteRoomDescriptorVersion(DESC, snapshot)).toMatchObject({ ok: false, reason: 'version_unavailable' });
        expect(h.fetch).toHaveBeenCalledOnce();
    });

    it('rejects stale versions belonging to another room or account pod', async () => {
        const h = harness((url) => url === descriptorUrl ? response(200, DESC, '"v1"') : null);
        const snapshot = await h.io.podReadRoomDescriptorVersion('empty-room');
        expect((await h.io.podWriteRoomDescriptorVersion(DESC, { ...snapshot, roomId: 'another' })).ok).toBe(false);
        expect((await h.io.podWriteRoomDescriptorVersion(DESC, { ...snapshot, url: 'https://other.example/rooms/empty-room/room.json' })).ok).toBe(false);
        expect(h.fetch).toHaveBeenCalledOnce();
    });

    it('treats malformed, mismatched, failed and oversized reads as unavailable', async () => {
        const results = [response(503), response(200, { ...DESC, room_id: 'wrong' }, '"v1"'),
            { ...response(200), text: async () => 'invalid' },
            { ...response(200), text: async () => 'a'.repeat(65537) }];
        for (const result of results) {
            const h = harness(() => result);
            expect((await h.io.podReadRoomDescriptorVersion('empty-room')).ok).toBe(false);
        }
    });
});
