// pod-sync.test.js — R62: opt-in bookmarks + settings sync to the pod. Pure
// mock (no live CSS): a fake solidSession records every fetch. Complements the
// live-CSS integration in web/pod.test.js and the manual npx-CSS recipe.
import { describe, it, expect, vi, beforeEach } from 'vitest';

let _session = null;
let _root = null;
let _calls = [];

vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _root,
}));

import {
    podSyncEnabled,
    podSyncSavedMessage,
    podSyncRemoveSavedMessage,
    podReadSavedMessages,
    podWriteSettings,
    podReadSettings,
} from './pod.js';

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

const SAVED = {
    id: 'm-book1', thread_id: 'general', thread_type: 'local_room',
    thread_label: 'general', from_name: 'Alice', content: 'pin this',
    has_file: false, file_kind: '', timestamp: '2026-07-20T14:00:00.000Z',
    addedAt: 1721480400000,
};

beforeEach(() => {
    _calls = [];
    _root = ROOT;
    _session = makeSession(true);
    setSync(false);
});

describe('podSyncEnabled', () => {
    it('defaults off and reflects the pref', () => {
        setSync(false); expect(podSyncEnabled()).toBe(false);
        setSync(true); expect(podSyncEnabled()).toBe(true);
    });
});

describe('bookmarks sync', () => {
    it('writes nothing when sync is OFF', async () => {
        setSync(false);
        await podSyncSavedMessage(SAVED);
        expect(_calls.filter(c => c.method === 'PUT')).toHaveLength(0);
    });

    it('writes nothing when not logged in', async () => {
        setSync(true);
        _session = makeSession(false);
        await podSyncSavedMessage(SAVED);
        expect(_session.fetch).not.toHaveBeenCalled();
    });

    it('writes a px:SavedMessage + index entry when ON + logged in', async () => {
        setSync(true);
        await podSyncSavedMessage(SAVED);
        const put = _calls.find(c => c.method === 'PUT' && c.url.endsWith('proxion/saved/m-book1.jsonld'));
        expect(put).toBeTruthy();
        const body = JSON.parse(put.body);
        expect(body['@type']).toBe('px:SavedMessage');
        expect(body['px:content']).toBe('pin this');
        expect(body['px:threadLabel']).toBe('general');
        expect(_calls.some(c => c.method === 'PUT' && c.url.endsWith('proxion/saved/index.jsonld'))).toBe(true);
    });

    it('round-trips an archived bookmark back to snapshot shape', async () => {
        setSync(true);
        const doc = {
            '@type': 'px:SavedMessage', 'px:messageId': 'm-book1',
            'px:threadId': 'general', 'px:threadType': 'local_room',
            'px:threadLabel': 'general', 'px:fromName': 'Alice',
            'px:content': 'pin this', 'px:hasFile': false, 'px:fileKind': '',
            'px:timestamp': SAVED.timestamp, 'px:savedAt': SAVED.addedAt,
        };
        _session = makeSession(true, async (url) => {
            if (url.endsWith('index.jsonld')) {
                return { ok: true, json: async () => ({ 'px:ids': ['m-book1'] }),
                         text: async () => JSON.stringify({ 'px:ids': ['m-book1'] }) };
            }
            return { ok: true, text: async () => JSON.stringify(doc) };
        });
        const rows = await podReadSavedMessages();
        expect(rows).toHaveLength(1);
        expect(rows[0]).toMatchObject({ id: 'm-book1', content: 'pin this', from_name: 'Alice' });
    });

    it('read returns [] when sync OFF', async () => {
        setSync(false);
        expect(await podReadSavedMessages()).toEqual([]);
    });

    it('remove deletes the doc and prunes the index', async () => {
        setSync(true);
        _session = makeSession(true, async (url, opts) => {
            if (url.endsWith('index.jsonld') && (!opts.method || opts.method === 'GET')) {
                return { ok: true, json: async () => ({ 'px:ids': ['m-book1', 'm-keep'] }),
                         text: async () => JSON.stringify({ 'px:ids': ['m-book1', 'm-keep'] }) };
            }
            return { ok: true, json: async () => ({}), text: async () => '{}' };
        });
        await podSyncRemoveSavedMessage('m-book1');
        expect(_calls.some(c => c.method === 'DELETE' && c.url.endsWith('proxion/saved/m-book1.jsonld'))).toBe(true);
        const idxPut = _calls.find(c => c.method === 'PUT' && c.url.endsWith('index.jsonld'));
        expect(JSON.parse(idxPut.body)['px:ids']).toEqual(['m-keep']);
    });
});

describe('settings sync', () => {
    it('writes px:Settings with the prefs object when ON', async () => {
        setSync(true);
        await podWriteSettings({ proxion_receipts_enabled: '1', proxion_locale: 'de' });
        const put = _calls.find(c => c.method === 'PUT' && c.url.endsWith('proxion/settings.jsonld'));
        expect(put).toBeTruthy();
        const body = JSON.parse(put.body);
        expect(body['@type']).toBe('px:Settings');
        expect(body['px:prefs']).toEqual({ proxion_receipts_enabled: '1', proxion_locale: 'de' });
    });

    it('writes nothing when sync OFF', async () => {
        setSync(false);
        await podWriteSettings({ proxion_locale: 'de' });
        expect(_calls.filter(c => c.method === 'PUT')).toHaveLength(0);
    });

    it('reads the prefs object back', async () => {
        setSync(true);
        _session = makeSession(true, async () => ({
            ok: true, json: async () => ({ '@type': 'px:Settings', 'px:prefs': { proxion_locale: 'fr' } }),
        }));
        expect(await podReadSettings()).toEqual({ proxion_locale: 'fr' });
    });

    it('read returns null when sync OFF', async () => {
        setSync(false);
        expect(await podReadSettings()).toBeNull();
    });
});
