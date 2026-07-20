// mute-sync.test.js — R64: opt-in mute-list sync to the pod. Pure mock.
// (Blocks were scoped out: the web client has no block feature to sync.)
import { describe, it, expect, vi, beforeEach } from 'vitest';

let _session = null;
let _root = null;
let _calls = [];

vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _root,
}));

import { podWriteMutes, podReadMutes } from './pod.js';

const ROOT = 'https://alice.pod.example/';

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

beforeEach(() => {
    _calls = [];
    _root = ROOT;
    _session = makeSession(true);
    setSync(true);
});

describe('podWriteMutes', () => {
    it('writes nothing when sync OFF', async () => {
        setSync(false);
        await podWriteMutes(['room-1']);
        expect(_calls.filter(c => c.method === 'PUT')).toHaveLength(0);
    });

    it('writes nothing when not logged in', async () => {
        _session = makeSession(false);
        await podWriteMutes(['room-1']);
        expect(_session.fetch).not.toHaveBeenCalled();
    });

    it('writes a px:MuteList with the thread ids when ON', async () => {
        await podWriteMutes(['room-1', 'dm-2']);
        const put = _calls.find(c => c.method === 'PUT' && c.url.endsWith('proxion/mutes.jsonld'));
        expect(put).toBeTruthy();
        const body = JSON.parse(put.body);
        expect(body['@type']).toBe('px:MuteList');
        expect(body['px:threads']).toEqual(['room-1', 'dm-2']);
    });

    it('filters non-string entries', async () => {
        await podWriteMutes(['room-1', 42, null, 'dm-2']);
        const put = _calls.find(c => c.method === 'PUT' && c.url.endsWith('proxion/mutes.jsonld'));
        expect(JSON.parse(put.body)['px:threads']).toEqual(['room-1', 'dm-2']);
    });
});

describe('podReadMutes', () => {
    it('reads the thread list back', async () => {
        _session = makeSession(true, async () => ({
            ok: true, json: async () => ({ '@type': 'px:MuteList', 'px:threads': ['room-1'] }),
        }));
        expect(await podReadMutes()).toEqual(['room-1']);
    });

    it('returns null when sync OFF', async () => {
        setSync(false);
        expect(await podReadMutes()).toBeNull();
    });

    it('returns null on a malformed doc', async () => {
        _session = makeSession(true, async () => ({ ok: true, json: async () => ({ '@type': 'px:MuteList' }) }));
        expect(await podReadMutes()).toBeNull();
    });
});
