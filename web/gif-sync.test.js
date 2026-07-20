// gif-sync.test.js — R63: opt-in GIF-tray sync to the pod. Pure mock (no live
// CSS): a fake solidSession records every fetch. Each favorite is a binary
// image resource + a px:GifFavorite metadata doc + an index entry.
import { describe, it, expect, vi, beforeEach } from 'vitest';

let _session = null;
let _root = null;
let _calls = [];

vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _root,
}));

import {
    podSyncGifFavorite,
    podSyncRemoveGifFavorite,
    podReadGifFavorites,
} from './pod.js';

const ROOT = 'https://alice.pod.example/';
// 1x1 transparent GIF, base64.
const GIF_B64 = 'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
const ID = 'a'.repeat(64); // sha256-shaped

function makeSession(loggedIn = true, fetchImpl = null) {
    return {
        info: { isLoggedIn: loggedIn, webId: 'https://alice.pod.example/profile/card#me' },
        fetch: vi.fn(async (url, opts = {}) => {
            _calls.push({ url, method: opts.method || 'GET', body: opts.body });
            if (fetchImpl) return fetchImpl(url, opts);
            return { ok: true, status: 200, json: async () => ({}), text: async () => '{}' };
        }),
    };
}

function setSync(on) {
    globalThis.localStorage = {
        _s: { proxion_pod_sync: on ? '1' : '0' },
        getItem(k) { return this._s[k] ?? null; },
        setItem(k, v) { this._s[k] = String(v); },
    };
}

const FAV = { id: ID, filename: 'blob.gif', mime: 'image/gif', data_b64: GIF_B64, addedAt: 1721480400000 };

beforeEach(() => {
    _calls = [];
    _root = ROOT;
    _session = makeSession(true);
    setSync(true);
});

describe('podSyncGifFavorite', () => {
    it('writes nothing when sync OFF', async () => {
        setSync(false);
        await podSyncGifFavorite(FAV);
        expect(_calls.filter(c => c.method === 'PUT')).toHaveLength(0);
    });

    it('writes nothing when not logged in', async () => {
        _session = makeSession(false);
        await podSyncGifFavorite(FAV);
        expect(_session.fetch).not.toHaveBeenCalled();
    });

    it('stores a binary image + px:GifFavorite doc + index entry', async () => {
        await podSyncGifFavorite(FAV);
        const imgPut = _calls.find(c => c.method === 'PUT' && c.url === `${ROOT}proxion/gifs/${ID}`);
        expect(imgPut).toBeTruthy();
        expect(imgPut.body).toBeInstanceOf(Uint8Array);      // real bytes, not base64 text
        const docPut = _calls.find(c => c.method === 'PUT' && c.url.endsWith(`proxion/gifs/${ID}.jsonld`));
        expect(docPut).toBeTruthy();
        const body = JSON.parse(docPut.body);
        expect(body['@type']).toBe('px:GifFavorite');
        expect(body['px:mime']).toBe('image/gif');
        expect(body['px:image']).toBe(`${ROOT}proxion/gifs/${ID}`);
        expect(_calls.some(c => c.method === 'PUT' && c.url.endsWith('proxion/gifs/index.jsonld'))).toBe(true);
    });

    it('rejects a disallowed mime', async () => {
        await podSyncGifFavorite({ ...FAV, mime: 'image/svg+xml' });
        expect(_calls.filter(c => c.method === 'PUT')).toHaveLength(0);
    });
});

describe('podReadGifFavorites', () => {
    it('round-trips index -> doc -> image -> row', async () => {
        const doc = {
            '@type': 'px:GifFavorite', 'px:gifId': ID, 'px:filename': 'blob.gif',
            'px:mime': 'image/gif', 'px:image': `${ROOT}proxion/gifs/${ID}`, 'px:addedAt': FAV.addedAt,
        };
        const imgBytes = Uint8Array.from(atob(GIF_B64), c => c.charCodeAt(0));
        _session = makeSession(true, async (url) => {
            if (url.endsWith('index.jsonld')) {
                return { ok: true, json: async () => ({ 'px:ids': [ID] }),
                         text: async () => JSON.stringify({ 'px:ids': [ID] }) };
            }
            if (url.endsWith('.jsonld')) return { ok: true, text: async () => JSON.stringify(doc) };
            return { ok: true, arrayBuffer: async () => imgBytes.buffer };
        });
        const rows = await podReadGifFavorites();
        expect(rows).toHaveLength(1);
        expect(rows[0]).toMatchObject({ id: ID, filename: 'blob.gif', mime: 'image/gif', data_b64: GIF_B64 });
    });

    it('returns [] when sync OFF', async () => {
        setSync(false);
        expect(await podReadGifFavorites()).toEqual([]);
    });

    it('skips a doc whose type is wrong', async () => {
        _session = makeSession(true, async (url) => {
            if (url.endsWith('index.jsonld')) {
                return { ok: true, json: async () => ({ 'px:ids': [ID] }),
                         text: async () => JSON.stringify({ 'px:ids': [ID] }) };
            }
            if (url.endsWith('.jsonld')) return { ok: true, text: async () => JSON.stringify({ '@type': 'px:Other' }) };
            return { ok: true, arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer };
        });
        expect(await podReadGifFavorites()).toEqual([]);
    });
});

describe('podSyncRemoveGifFavorite', () => {
    it('deletes both resources and prunes the index', async () => {
        _session = makeSession(true, async (url, opts) => {
            if (url.endsWith('index.jsonld') && (!opts.method || opts.method === 'GET')) {
                return { ok: true, json: async () => ({ 'px:ids': [ID, 'keep'] }),
                         text: async () => JSON.stringify({ 'px:ids': [ID, 'keep'] }) };
            }
            return { ok: true, json: async () => ({}), text: async () => '{}' };
        });
        await podSyncRemoveGifFavorite(ID);
        expect(_calls.some(c => c.method === 'DELETE' && c.url === `${ROOT}proxion/gifs/${ID}`)).toBe(true);
        expect(_calls.some(c => c.method === 'DELETE' && c.url.endsWith(`${ID}.jsonld`))).toBe(true);
        const idxPut = _calls.find(c => c.method === 'PUT' && c.url.endsWith('index.jsonld'));
        expect(JSON.parse(idxPut.body)['px:ids']).toEqual(['keep']);
    });
});
