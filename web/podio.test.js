// podio.js: the drop-box / identity / presence I/O over an injected fetch. The
// browser build (pod.js over solidSession) and the desktop app (gwpod.js over the
// gateway's pod bridge) both run this, so these pin the shared behaviour.
import { describe, it, expect, vi } from 'vitest';
import { createPodIo, dmInboxUrlFor, presenceUrlFor } from './podio.js';

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
