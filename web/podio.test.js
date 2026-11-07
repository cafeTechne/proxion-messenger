// podio.js: the drop-box / identity / presence I/O over an injected fetch. The
// browser build (pod.js over solidSession) and the desktop app (gwpod.js over the
// gateway's pod bridge) both run this, so these pin the shared behaviour.
import { describe, it, expect, vi } from 'vitest';
import { createPodIo, dmInboxUrlFor, presenceUrlFor } from './podio.js';
import { LEGACY_JOINED_ROOMS_UNREADABLE } from './webrooms.js';

const ROOT = 'https://pod.example/alice/';
const ME = 'https://pod.example/alice/profile/card#me';

function res(status, body = '', headers = {}) {
    const h = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
    return {
        ok: status >= 200 && status < 300, status,
        headers: { get: (k) => h[k.toLowerCase()] ?? null },
        text: async () => body,
        json: async () => JSON.parse(body),
    };
}

function io(fetch, { loggedIn = true, root = ROOT } = {}) {
    return createPodIo({ fetch, isLoggedIn: () => loggedIn, storageRoot: () => root, webId: () => ME });
}

describe('createPodIo', () => {
    it('does nothing without a session', async () => {
        const fetch = vi.fn();
        const p = io(fetch, { loggedIn: false });
        expect(await p.podEnsureDmInbox()).toBe(null);
        expect(await p.podDropDm('https://pod.example/bob/', { a: 1 })).toBe(false);
        expect(await p.podReadDmDrops()).toEqual([]);
        expect(await p.podDeleteDmDrop(ROOT + 'proxion/dm-inbox/x')).toBe(false);
        expect(await p.podWritePresence('online')).toBe(false);
        expect(fetch).not.toHaveBeenCalled();
    });

    it('creates the DM inbox as a container with a public-Append ACL', async () => {
        const fetch = vi.fn(async (url, opts = {}) => {
            if (opts.method === 'HEAD') return res(200, '', { link: '<.acl>; rel="acl"' });
            return res(201);
        });
        const url = await io(fetch).podEnsureDmInbox();
        expect(url).toBe(ROOT + 'proxion/dm-inbox/');
        const [putUrl, putOpts] = fetch.mock.calls[0];
        expect(putUrl).toBe(url);
        expect(putOpts.headers.Link).toMatch(/BasicContainer/);
        const acl = fetch.mock.calls.find(([u, o]) => o && o.method === 'PUT' && u.endsWith('.acl'));
        expect(acl[1].body).toMatch(/foaf:Agent; acl:accessTo <[^>]+>; acl:mode acl:Append/);
        expect(acl[1].body).toContain(`acl:agent <${ME}>`);
    });

    it('drops to a peer with no redirects, and refuses a private peer host', async () => {
        const fetch = vi.fn(async () => res(201));
        const p = io(fetch);
        expect(await p.podDropDm('https://pod.example/bob/', { v: 1 })).toBe(true);
        expect(fetch.mock.calls[0][0]).toBe('https://pod.example/bob/proxion/dm-inbox/');
        expect(fetch.mock.calls[0][1].redirect).toBe('error');
        expect(await p.podDropDm('https://10.0.0.5/bob/', { v: 1 })).toBe(false);
        expect(fetch).toHaveBeenCalledTimes(1);
    });

    it('reads drops and skips one that is over the size cap', async () => {
        const listing = JSON.stringify({ '@id': ROOT + 'proxion/dm-inbox/', contains: [
            { '@id': ROOT + 'proxion/dm-inbox/a' }, { '@id': ROOT + 'proxion/dm-inbox/b' }] });
        const fetch = vi.fn(async (url) => {
            if (url.endsWith('/dm-inbox/')) return res(200, listing);
            if (url.endsWith('/a')) return res(200, JSON.stringify({ from_webid: 'x' }));
            return res(200, 'x'.repeat(70000));
        });
        const drops = await io(fetch).podReadDmDrops();
        expect(drops).toEqual([{ url: ROOT + 'proxion/dm-inbox/a', envelope: { from_webid: 'x' } }]);
    });

    it('publishes the signer with a public-read ACL and no account did by default', async () => {
        const fetch = vi.fn(async (url, opts = {}) => {
            if (opts.method === 'HEAD') return res(200);
            if (!opts.method && url.endsWith('.acl')) return res(200, 'acl:Read');
            return res(201);
        });
        expect(await io(fetch).podPublishSigner('did:key:zDev')).toBe(true);
        const put = fetch.mock.calls[0];
        expect(put[0]).toBe(ROOT + 'proxion/identity/signer.json');
        expect(JSON.parse(put[1].body)).toEqual({ version: 1, signer: 'did:key:zDev', account_did: null });
    });

    it('reads presence with the server Last-Modified', async () => {
        const lm = 'Tue, 01 Sep 2026 10:00:00 GMT';
        const fetch = vi.fn(async () => res(200, JSON.stringify({ v: 1, status: 'online', heartbeat: 5 }), { 'Last-Modified': lm }));
        const d = await io(fetch).podReadPresence('https://pod.example/bob/');
        expect(d).toEqual({ status: 'online', heartbeat: 5, serverMs: Date.parse(lm) });
        expect(fetch.mock.calls[0][0]).toBe(presenceUrlFor('https://pod.example/bob/'));
    });

    it('builds the same box URLs as before', () => {
        expect(dmInboxUrlFor('https://pod.example/bob')).toBe('https://pod.example/bob/proxion/dm-inbox/');
        expect(dmInboxUrlFor(null)).toBe(null);
    });
});


describe('pending room authorization', () => {
    const owner = 'https://pod.example/bob/profile/card#me';
    const marker = { 'px:roomId': 'room-1', 'px:ownerWebId': owner };

    it('checks marker PUT success and stores the record as opaque JSON', async () => {
        const fetch = vi.fn(async () => res(201));
        expect(await io(fetch).podWritePendingJoin('room-1', owner)).toBe(true);
        const [url, opts] = fetch.mock.calls[0];
        expect(url).toMatch(/join-requests\/[0-9a-f]{64}\.jsonld$/);
        expect(opts.headers['Content-Type']).toBe('application/json');
        expect(JSON.parse(opts.body)).toMatchObject(marker);
        fetch.mockResolvedValue(res(503));
        expect(await io(fetch).podWritePendingJoin('room-1', owner)).toBe(false);
        expect(await io(fetch, { loggedIn: false }).podWritePendingJoin('room-1', owner)).toBe(false);
    });

    it('distinguishes absent and mismatched requests from temporarily unreadable ones', async () => {
        const fetch = vi.fn(async () => res(404));
        const p = io(fetch);
        expect(await p.podHasPendingJoin('room-1', owner)).toBe(false);
        fetch.mockResolvedValue(res(503));
        expect(await p.podHasPendingJoin('room-1', owner)).toBe(null);
        fetch.mockRejectedValue(new Error('offline'));
        expect(await p.podHasPendingJoin('room-1', owner)).toBe(null);
        fetch.mockResolvedValue(res(200, '{invalid'));
        expect(await p.podHasPendingJoin('room-1', owner)).toBe(null);
        fetch.mockResolvedValue(res(200, 'x'.repeat(65537)));
        expect(await p.podHasPendingJoin('room-1', owner)).toBe(null);
        fetch.mockResolvedValue(res(200, JSON.stringify(marker)));
        expect(await p.podHasPendingJoin('room-2', owner)).toBe(false);
        expect(await p.podHasPendingJoin('room-1', owner)).toBe(true);
    });

    it('accepts legacy expanded markers and never mixes fields from different RDF subjects', async () => {
        const px = 'https://proxion.dev/vocab/v1#';
        const expanded = { [px + 'roomId']: [{ '@value': 'room-1' }], [px + 'ownerWebId']: [{ '@value': owner }] };
        const fetch = vi.fn(async () => res(200, JSON.stringify([expanded])));
        const p = io(fetch);
        expect(await p.podHasPendingJoin('room-1', owner)).toBe(true);
        fetch.mockResolvedValue(res(200, JSON.stringify({ '@graph': [expanded] })));
        expect(await p.podHasPendingJoin('room-1', owner)).toBe(true);
        fetch.mockResolvedValue(res(200, JSON.stringify([
            { [px + 'roomId']: [{ '@value': 'room-1' }] },
            { [px + 'ownerWebId']: [{ '@value': owner }] },
        ])));
        expect(await p.podHasPendingJoin('room-1', owner)).toBe(false);
    });
});

describe('joined room records', () => {
    const rooms = [{ roomId: 'room-1', owner: 'https://pod.example/bob/profile/card#me',
        ownerPodRoot: 'https://pod.example/bob/', container: 'https://pod.example/bob/proxion/rooms/room-1/', title: 'Room' }];
    const canonical = ROOT + 'proxion/joined-rooms.json';
    const legacy = ROOT + 'proxion/joined-rooms.jsonld';
    const px = 'https://proxion.dev/vocab/v1#';
    const empty = { '@id': legacy, '@type': [px + 'JoinedRooms'],
        [px + 'updatedAt']: [{ '@value': '2026-11-01T00:00:00Z' }] };
    const lost = [{ ...empty, [px + 'rooms']: [{ '@id': '_:lost-record' }] }];

    it('returns the canonical representation version and reads past browser caches', async () => {
        const fetch = vi.fn(async () => res(200, JSON.stringify({ 'px:rooms': rooms }), { ETag: '"rooms-1"' }));
        expect(await io(fetch).podReadJoinedRoomsVersion()).toEqual({
            records: rooms, exists: true, etag: '"rooms-1"', resource: canonical,
        });
        expect(fetch).toHaveBeenCalledWith(canonical, expect.objectContaining({ cache: 'no-store' }));
    });

    it('guards new canonical records with If-None-Match and existing records with their strong ETag', async () => {
        const fetch = vi.fn(async () => res(201));
        const p = io(fetch);
        expect(await p.podWriteJoinedRoomsVersion(rooms, { exists: false, resource: canonical })).toEqual({ ok: true, conflict: false });
        expect(fetch.mock.calls[0][1].headers['If-None-Match']).toBe('*');
        expect(await p.podWriteJoinedRoomsVersion(rooms, { exists: true, etag: '"rooms-1"', resource: canonical })).toEqual({ ok: true, conflict: false });
        expect(fetch.mock.calls[1][1].headers['If-Match']).toBe('"rooms-1"');
        fetch.mockResolvedValue(res(412));
        expect(await p.podWriteJoinedRoomsVersion(rooms, { exists: true, etag: '"rooms-1"', resource: canonical })).toEqual({ ok: false, conflict: true });
        fetch.mockResolvedValue(res(503));
        expect(await p.podWriteJoinedRoomsVersion(rooms, { exists: true, etag: '"rooms-1"', resource: canonical })).toEqual({ ok: false, conflict: false });
    });

    it('never sends an unguarded write when the ETag or account snapshot is unusable', async () => {
        const fetch = vi.fn(async () => res(201));
        const p = io(fetch);
        for (const etag of [null, '', 'W/"weak"', '*', 'unquoted', '"injected\r\nvalue"', '"' + 'x'.repeat(511) + '"']) {
            expect((await p.podWriteJoinedRoomsVersion(rooms, { exists: true, etag, resource: canonical })).ok).toBe(false);
        }
        expect((await p.podWriteJoinedRoomsVersion(rooms, { exists: false, resource: 'https://other.example/proxion/joined-rooms.json' })).ok).toBe(false);
        expect(fetch).not.toHaveBeenCalled();
    });

    it('uses the absent canonical version when reading a legacy document', async () => {
        const fetch = vi.fn(async (url) => url === canonical ? res(404)
            : res(200, JSON.stringify({ 'px:rooms': rooms }), { ETag: '"legacy-1"' }));
        expect(await io(fetch).podReadJoinedRoomsVersion()).toEqual({ records: rooms, exists: false, etag: null, resource: canonical });
    });

    it('round-trips opaque canonical JSON through a fresh I/O instance', async () => {
        const files = new Map();
        const fetch = vi.fn(async (url, opts = {}) => {
            if (opts.method === 'PUT') {
                expect(url).toBe(canonical);
                expect(opts.headers['Content-Type']).toBe('application/json');
                files.set(url, opts.body);
                return res(201);
            }
            return files.has(url) ? res(200, files.get(url)) : res(404);
        });
        expect(await io(fetch).podWriteJoinedRooms(rooms)).toBe(true);
        expect(await io(fetch).podReadJoinedRooms()).toEqual(rooms);
        expect(files.has(legacy)).toBe(false);
    });

    it('falls back to a valid legacy list and migrates into a separate canonical file', async () => {
        const original = JSON.stringify({ 'px:rooms': rooms });
        const files = new Map([[legacy, original]]);
        const fetch = vi.fn(async (url, opts = {}) => {
            if (opts.method === 'PUT') { files.set(url, opts.body); return res(201); }
            return files.has(url) ? res(200, files.get(url)) : res(404);
        });
        const oldRooms = await io(fetch).podReadJoinedRooms();
        expect(oldRooms).toEqual(rooms);
        expect(await io(fetch).podWriteJoinedRooms(oldRooms)).toBe(true);
        expect(await io(fetch).podReadJoinedRooms()).toEqual(rooms);
        expect(files.get(legacy)).toBe(original);
    });

    it('signals recognized lost legacy fields so cached records can be migrated without modifying the original', async () => {
        const original = JSON.stringify(lost);
        const files = new Map([[legacy, original]]);
        const fetch = vi.fn(async (url, opts = {}) => {
            if (opts.method === 'PUT') { files.set(url, opts.body); return res(201); }
            return files.has(url) ? res(200, files.get(url)) : res(404);
        });
        expect(await io(fetch).podReadJoinedRooms()).toBe(LEGACY_JOINED_ROOMS_UNREADABLE);
        // The store supplies its validated WebID-scoped cache for migration.
        expect(await io(fetch).podWriteJoinedRooms(rooms)).toBe(true);
        expect(await io(fetch).podReadJoinedRooms()).toEqual(rooms);
        expect(files.get(legacy)).toBe(original);
        expect(fetch.mock.calls.filter(([, opts]) => opts?.method === 'PUT').map(([url]) => url)).toEqual([canonical]);
    });

    it('recognizes a typed empty legacy RDF document in array and graph forms', async () => {
        const fetch = vi.fn(async (url) => url === canonical ? res(404) : res(200, JSON.stringify([empty])));
        expect(await io(fetch).podReadJoinedRooms()).toEqual([]);
        fetch.mockImplementation(async (url) => url === canonical ? res(404) : res(200, JSON.stringify({ '@graph': [empty] })));
        expect(await io(fetch).podReadJoinedRooms()).toEqual([]);
    });

    it('uses the canonical list as authority and only falls back on a 404', async () => {
        const fetch = vi.fn(async (url) => res(200, JSON.stringify({ 'px:rooms': url === canonical ? [] : rooms })));
        expect(await io(fetch).podReadJoinedRooms()).toEqual([]);
        expect(fetch).toHaveBeenCalledTimes(1);
        fetch.mockClear().mockImplementation(async (url) => url === canonical ? res(503) : res(200, JSON.stringify({ 'px:rooms': rooms })));
        expect(await io(fetch).podReadJoinedRooms()).toBe(null);
        expect(fetch).toHaveBeenCalledTimes(1);
        fetch.mockClear().mockImplementation(async (url) => url === canonical ? res(200, JSON.stringify(lost)) : res(200, JSON.stringify({ 'px:rooms': rooms })));
        expect(await io(fetch).podReadJoinedRooms()).toBe(null);
        expect(fetch).toHaveBeenCalledTimes(1);
    });

    it('keeps unknown or corrupt shapes unavailable and distinguishes a missing list', async () => {
        const fetch = vi.fn(async () => res(200, JSON.stringify({ unexpected: [] })));
        expect(await io(fetch).podReadJoinedRooms()).toBe(null);
        fetch.mockImplementation(async (url) => url === canonical ? res(404) : res(200, JSON.stringify({ 'px:rooms': [{ '@id': '_:lost-record' }] })));
        expect(await io(fetch).podReadJoinedRooms()).toBe(null);
        fetch.mockImplementation(async (url) => url === canonical ? res(404) : res(200, JSON.stringify({ '@type': 'px:JoinedRooms', 'px:rooms': 'corrupt' })));
        expect(await io(fetch).podReadJoinedRooms()).toBe(null);
        fetch.mockResolvedValue(res(404));
        expect(await io(fetch).podReadJoinedRooms()).toEqual([]);
    });
});
