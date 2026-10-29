// dm-index.test.js: the pod DM thread index keeps web-build thread ids, which
// are the peer's WebID, across a reload (and across later index updates).
import { describe, it, expect, vi, beforeEach } from 'vitest';

let _session = null;
const _root = 'https://me.pod/';
vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _root,
}));

import { podReadDmIndex, _podUpdateDmIndex } from './pod.js';

const INDEX = 'https://me.pod/proxion/dm/index.jsonld';
const BOB = 'https://bob.pod/profile/card#me';
const LOCAL = 'http://localhost:3000/carol/profile/card#me';

function sessionWithIndex(ids) {
    const store = { [INDEX]: ids ? { 'px:ids': ids } : null };
    return {
        store,
        info: { isLoggedIn: true, webId: 'https://me.pod/profile/card#me' },
        fetch: vi.fn(async (url, opts = {}) => {
            if (opts.method === 'PUT') { store[url] = JSON.parse(opts.body); return { ok: true, status: 201 }; }
            const doc = store[url];
            if (!doc) return { ok: false, status: 404, json: async () => ({}) };
            return { ok: true, status: 200, json: async () => doc };
        }),
    };
}

beforeEach(() => { _session = null; });

describe('pod DM index', () => {
    it('reads WebID thread ids back, alongside plain ids', async () => {
        _session = sessionWithIndex(['cert-123', BOB, LOCAL]);
        expect(await podReadDmIndex()).toEqual(['cert-123', BOB, LOCAL]);
    });

    it('drops ids that are neither safe ids nor DM WebIDs', async () => {
        _session = sessionWithIndex([BOB, 'http://evil.example/profile/card#me', '../x', 'javascript:alert(1)', 42]);
        expect(await podReadDmIndex()).toEqual([BOB]);
    });

    it('adding a thread keeps the WebID threads already listed', async () => {
        _session = sessionWithIndex([BOB]);
        await _podUpdateDmIndex(LOCAL, true);
        expect(_session.store[INDEX]['px:ids']).toEqual([BOB, LOCAL]);
        await _podUpdateDmIndex(BOB, false);
        expect(_session.store[INDEX]['px:ids']).toEqual([LOCAL]);
    });

    it('refuses to add an id that is not a DM WebID or safe id', async () => {
        _session = sessionWithIndex([BOB]);
        await _podUpdateDmIndex('http://evil.example/x', true);
        expect(_session.store[INDEX]['px:ids']).toEqual([BOB]);
    });
});
