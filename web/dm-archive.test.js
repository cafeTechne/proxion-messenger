// dm-archive.test.js — R61: opt-in DM archive to the pod. Pure-mock unit tests
// (no live CSS): a fake solidSession records every fetch, so we can assert the
// exact requests. Complements web/pod.test.js (live-CSS integration, skipped
// without TEST_CSS_CLIENT_ID) and the manual npx-CSS recipe in the docs.
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Reassignable mock state, captured by the auth.js mock factory by reference.
let _session = null;
let _root = null;
let _calls = [];

vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _root,
}));

import {
    dmPodArchiveEnabled,
    podArchiveDmMessage,
    podArchiveDeleteDmMessage,
    podReadDmMessages,
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

function setPref(on) {
    globalThis.localStorage = {
        _s: { proxion_dm_pod_archive: on ? '1' : '0' },
        getItem(k) { return this._s[k] ?? null; },
        setItem(k, v) { this._s[k] = String(v); },
    };
}

const MSG = {
    message_id: 'm-abc123', thread_id: 'dm-thread1',
    content: 'secret plaintext', from_webid: 'https://alice.pod.example/profile/card#me',
    from_display_name: 'Alice', timestamp: '2026-07-20T14:00:00.000Z', reply_to_id: null,
};

beforeEach(() => {
    _calls = [];
    _root = ROOT;
    _session = makeSession(true);
    setPref(false);
});

describe('dmPodArchiveEnabled', () => {
    it('defaults off and reflects the pref', () => {
        setPref(false);
        expect(dmPodArchiveEnabled()).toBe(false);
        setPref(true);
        expect(dmPodArchiveEnabled()).toBe(true);
    });
});

describe('podArchiveDmMessage', () => {
    it('writes nothing when the archive pref is OFF', async () => {
        setPref(false);
        await podArchiveDmMessage('dm-thread1', MSG);
        expect(_calls.filter(c => c.method === 'PUT')).toHaveLength(0);
    });

    it('writes nothing when not logged into a pod', async () => {
        setPref(true);
        _session = makeSession(false);
        await podArchiveDmMessage('dm-thread1', MSG);
        expect(_session.fetch).not.toHaveBeenCalled();
    });

    it('archives the plaintext as px:Message JSON-LD when ON + logged in', async () => {
        setPref(true);
        await podArchiveDmMessage('dm-thread1', MSG);
        const put = _calls.find(c =>
            c.method === 'PUT' && c.url.endsWith('proxion/dm/dm-thread1/messages/m-abc123.jsonld'));
        expect(put).toBeTruthy();
        const body = JSON.parse(put.body);
        expect(body['@type']).toBe('px:Message');
        expect(body['px:content']).toBe('secret plaintext');
        expect(body['px:fromWebid']).toBe(MSG.from_webid);
        // per-thread message index is maintained for read-back
        expect(_calls.some(c =>
            c.method === 'PUT' && c.url.endsWith('proxion/dm/dm-thread1/messages/index.jsonld'))).toBe(true);
    });

    it('ignores unsafe ids', async () => {
        setPref(true);
        await podArchiveDmMessage('../evil', { ...MSG, message_id: 'ok' });
        await podArchiveDmMessage('dm-thread1', { ...MSG, message_id: 'has/slash' });
        expect(_calls.filter(c => c.method === 'PUT')).toHaveLength(0);
    });
});

describe('podReadDmMessages', () => {
    it('round-trips archived docs back into message shape', async () => {
        const archived = {
            '@type': 'px:Message',
            'px:messageId': 'm-abc123',
            'px:content': 'secret plaintext',
            'px:contentType': 'text',
            'px:fromWebid': MSG.from_webid,
            'px:fromName': 'Alice',
            'px:timestamp': MSG.timestamp,
            'px:replyToId': null,
        };
        _session = makeSession(true, async (url) => {
            if (url.endsWith('index.jsonld')) {
                return { ok: true, json: async () => ({ 'px:ids': ['m-abc123'] }),
                         text: async () => JSON.stringify({ 'px:ids': ['m-abc123'] }) };
            }
            return { ok: true, text: async () => JSON.stringify(archived) };
        });
        const msgs = await podReadDmMessages('dm-thread1');
        expect(msgs).toHaveLength(1);
        expect(msgs[0]).toMatchObject({
            message_id: 'm-abc123', thread_id: 'dm-thread1',
            content: 'secret plaintext', from_webid: MSG.from_webid,
            from_display_name: 'Alice', timestamp: MSG.timestamp,
        });
    });

    it('returns [] when not logged in', async () => {
        _session = makeSession(false);
        expect(await podReadDmMessages('dm-thread1')).toEqual([]);
    });

    it('rejects a doc whose type is not px:Message', async () => {
        _session = makeSession(true, async (url) => {
            if (url.endsWith('index.jsonld')) {
                return { ok: true, json: async () => ({ 'px:ids': ['m-x'] }),
                         text: async () => JSON.stringify({ 'px:ids': ['m-x'] }) };
            }
            return { ok: true, text: async () => JSON.stringify({ '@type': 'px:Other', 'px:messageId': 'm-x' }) };
        });
        expect(await podReadDmMessages('dm-thread1')).toEqual([]);
    });
});

describe('podArchiveDeleteDmMessage', () => {
    it('deletes the doc and removes it from the thread index', async () => {
        _session = makeSession(true, async (url, opts) => {
            if (url.endsWith('index.jsonld') && (!opts.method || opts.method === 'GET')) {
                return { ok: true, json: async () => ({ 'px:ids': ['m-abc123', 'm-keep'] }),
                         text: async () => JSON.stringify({ 'px:ids': ['m-abc123', 'm-keep'] }) };
            }
            return { ok: true, json: async () => ({}), text: async () => '{}' };
        });
        await podArchiveDeleteDmMessage('dm-thread1', 'm-abc123');
        expect(_calls.some(c =>
            c.method === 'DELETE' && c.url.endsWith('proxion/dm/dm-thread1/messages/m-abc123.jsonld'))).toBe(true);
        const indexPut = _calls.find(c =>
            c.method === 'PUT' && c.url.endsWith('index.jsonld'));
        expect(indexPut).toBeTruthy();
        expect(JSON.parse(indexPut.body)['px:ids']).toEqual(['m-keep']);
    });
});
