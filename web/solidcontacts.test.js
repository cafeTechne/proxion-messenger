// solidcontacts.test.js — reading the standard Solid social graph (foaf:knows +
// names) for contact import. Pure mock: a fake session returns canned JSON-LD.
import { describe, it, expect, vi, beforeEach } from 'vitest';

let _session = null;
let _root = 'https://me.pod/';
vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _root,
}));

import { podReadKnownWebIds, podResolveWebIdName, podImportContacts } from './pod.js';

const ME = 'https://me.pod/profile/card#me';
const ALICE = 'https://alice.pod/profile/card#me';
const BOB = 'https://bob.pod/profile/card#me';

// Map a URL to the JSON-LD a server would return for it.
function sessionServing(map) {
    return {
        info: { isLoggedIn: true, webId: ME },
        fetch: vi.fn(async (url) => {
            const doc = map[url];
            if (!doc) return { ok: false, status: 404, json: async () => ({}) };
            return { ok: true, status: 200, json: async () => doc };
        }),
    };
}

beforeEach(() => { _session = null; _root = 'https://me.pod/'; });

describe('podReadKnownWebIds', () => {
    it('reads foaf:knows WebIDs from a profile (array form)', async () => {
        _session = sessionServing({
            [ME]: {
                '@id': ME,
                'http://xmlns.com/foaf/0.1/knows': [{ '@id': ALICE }, { '@id': BOB }],
            },
        });
        expect((await podReadKnownWebIds()).sort()).toEqual([ALICE, BOB].sort());
    });

    it('tolerates a single (non-array) knows value and @graph shape', async () => {
        _session = sessionServing({
            [ME]: { '@graph': [{ '@id': ME, 'http://xmlns.com/foaf/0.1/knows': { '@id': ALICE } }] },
        });
        expect(await podReadKnownWebIds()).toEqual([ALICE]);
    });

    it('ignores non-http knows values (e.g. blank nodes)', async () => {
        _session = sessionServing({
            [ME]: { '@id': ME, 'http://xmlns.com/foaf/0.1/knows': [{ '@id': '_:b0' }, { '@id': ALICE }] },
        });
        expect(await podReadKnownWebIds()).toEqual([ALICE]);
    });

    it('reads a different profile when given a URL', async () => {
        _session = sessionServing({
            [ALICE]: { '@id': ALICE, 'http://xmlns.com/foaf/0.1/knows': [{ '@id': BOB }] },
        });
        expect(await podReadKnownWebIds(ALICE)).toEqual([BOB]);
    });

    it('returns [] on a missing profile or when logged out', async () => {
        _session = sessionServing({});
        expect(await podReadKnownWebIds()).toEqual([]);
        _session = { info: { isLoggedIn: false } };
        expect(await podReadKnownWebIds()).toEqual([]);
    });
});

describe('podResolveWebIdName', () => {
    it('reads foaf:name', async () => {
        _session = sessionServing({ [ALICE]: { '@id': ALICE, 'http://xmlns.com/foaf/0.1/name': [{ '@value': 'Alice A' }] } });
        expect(await podResolveWebIdName(ALICE)).toBe('Alice A');
    });
    it('falls back to vcard:fn', async () => {
        _session = sessionServing({ [BOB]: { '@id': BOB, 'http://www.w3.org/2006/vcard/ns#fn': 'Bob B' } });
        expect(await podResolveWebIdName(BOB)).toBe('Bob B');
    });
    it('returns empty string when there is no name', async () => {
        _session = sessionServing({ [ALICE]: { '@id': ALICE } });
        expect(await podResolveWebIdName(ALICE)).toBe('');
    });
});

describe('podImportContacts', () => {
    it('pairs each known WebID with its resolved name', async () => {
        _session = sessionServing({
            [ME]: { '@id': ME, 'http://xmlns.com/foaf/0.1/knows': [{ '@id': ALICE }, { '@id': BOB }] },
            [ALICE]: { '@id': ALICE, 'http://xmlns.com/foaf/0.1/name': 'Alice A' },
            [BOB]: { '@id': BOB, 'http://xmlns.com/foaf/0.1/name': 'Bob B' },
        });
        const contacts = await podImportContacts();
        expect(contacts).toContainEqual({ webid: ALICE, name: 'Alice A' });
        expect(contacts).toContainEqual({ webid: BOB, name: 'Bob B' });
    });
    it('leaves the name empty for a contact whose card has none', async () => {
        _session = sessionServing({
            [ME]: { '@id': ME, 'http://xmlns.com/foaf/0.1/knows': { '@id': ALICE } },
            [ALICE]: { '@id': ALICE },
        });
        expect(await podImportContacts()).toEqual([{ webid: ALICE, name: '' }]);
    });
});
