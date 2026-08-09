// profile-card.test.js — R100/A1: publish the display name into the standard
// WebID card (foaf:name + vcard:fn) so other Solid apps show a name. Pure-mock.
import { describe, it, expect, vi, beforeEach } from 'vitest';

let _session = null;

vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => 'https://alice.example/',
}));

import {
    buildProfileNamePatch, extractFoafName, podEnsureProfileName,
} from './pod.js';

const WEBID = 'https://alice.example/profile/card#me';

describe('buildProfileNamePatch', () => {
    it('inserts foaf:name + vcard:fn when there is no prior name', () => {
        const p = buildProfileNamePatch({ webId: WEBID, name: 'Alice' });
        expect(p).not.toMatch(/DELETE DATA/);
        expect(p).toMatch(/INSERT DATA/);
        expect(p).toContain(`<${WEBID}> <http://xmlns.com/foaf/0.1/name> "Alice"`);
        expect(p).toContain(`<http://www.w3.org/2006/vcard/ns#fn> "Alice"`);
    });

    it('deletes the prior name then inserts the new one (upsert)', () => {
        const p = buildProfileNamePatch({ webId: WEBID, name: 'Alice B', prevName: 'Alice' });
        expect(p).toMatch(/DELETE DATA[\s\S]*"Alice"[\s\S]*INSERT DATA[\s\S]*"Alice B"/);
    });

    it('escapes quotes and backslashes in the name', () => {
        const p = buildProfileNamePatch({ webId: WEBID, name: 'A "B" \\C' });
        expect(p).toContain('"A \\"B\\" \\\\C"');
    });
});

describe('extractFoafName', () => {
    it('reads a plain string name from a @graph card', () => {
        const json = { '@graph': [{ '@id': WEBID, 'http://xmlns.com/foaf/0.1/name': 'Alice' }] };
        expect(extractFoafName(json, WEBID)).toBe('Alice');
    });
    it('reads a {@value} object and the compact predicate', () => {
        const json = [{ '@id': WEBID, 'foaf:name': { '@value': 'Bob' } }];
        expect(extractFoafName(json, WEBID)).toBe('Bob');
    });
    it('matches a node by #me suffix when the exact id differs', () => {
        const json = [{ '@id': 'https://x/profile/card#me', 'name': 'Carol' }];
        expect(extractFoafName(json, WEBID)).toBe('Carol');
    });
    it('returns null when there is no name', () => {
        expect(extractFoafName([{ '@id': WEBID }], WEBID)).toBeNull();
        expect(extractFoafName(null, WEBID)).toBeNull();
    });
});

describe('podEnsureProfileName', () => {
    beforeEach(() => { _session = null; });

    it('is a no-op when not logged in', async () => {
        _session = { info: { isLoggedIn: false, webId: WEBID }, fetch: vi.fn() };
        expect(await podEnsureProfileName('Alice')).toBe(false);
        expect(_session.fetch).not.toHaveBeenCalled();
    });

    it('does not PATCH when the card name already matches', async () => {
        const patches = [];
        _session = {
            info: { isLoggedIn: true, webId: WEBID },
            fetch: vi.fn(async (url, opts) => {
                if (opts && opts.method === 'PATCH') { patches.push(opts.body); return { ok: true }; }
                return { ok: true, json: async () => ({ '@id': WEBID, 'http://xmlns.com/foaf/0.1/name': 'Alice' }) };
            }),
        };
        expect(await podEnsureProfileName('Alice')).toBe(true);
        expect(patches).toHaveLength(0);
    });

    it('PATCHes the card doc when the name is absent or different', async () => {
        const patches = [];
        _session = {
            info: { isLoggedIn: true, webId: WEBID },
            fetch: vi.fn(async (url, opts) => {
                if (opts && opts.method === 'PATCH') {
                    expect(url).toBe('https://alice.example/profile/card');   // fragment stripped
                    patches.push(opts.body); return { ok: true };
                }
                return { ok: true, json: async () => ({ '@id': WEBID }) };   // no name yet
            }),
        };
        expect(await podEnsureProfileName('Alice')).toBe(true);
        expect(patches).toHaveLength(1);
        expect(patches[0]).toContain('"Alice"');
    });
});
