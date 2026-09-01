// pod-ssrf.test.js — SSRF gating of the cross-pod (*At / peer) helpers. Pure mock
// (no live CSS): a fake solidSession records every fetch, so we can assert both
// that a private-host target is refused BEFORE any authenticated fetch, and that
// peer fetches ask fetch never to follow a cross-origin redirect (redirect:'error').
import { describe, it, expect, vi, beforeEach } from 'vitest';

let _session = null;
let _root = 'https://me.pod.example/';
let _calls = [];

vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _root,
}));

import {
    podReadRoomDescriptorAt,
    podReadChatDayAt,
    podReadChatRecentAt,
    podWriteChatMessageAt,
    podReadPresence,
    podFetchPeerSigner,
    podDropDm,
    podUploadVoiceAudio,
    podDeleteVoiceAudio,
    podUploadFile,
} from './pod.js';

function makeSession(fetchImpl = null) {
    return {
        info: { isLoggedIn: true, webId: 'https://me.pod.example/profile/card#me' },
        fetch: vi.fn(async (url, opts = {}) => {
            _calls.push({ url, opts });
            if (fetchImpl) return fetchImpl(url, opts);
            return { ok: true, status: 200, json: async () => ({}), text: async () => '{}' };
        }),
    };
}

beforeEach(() => {
    _calls = [];
    _root = 'https://me.pod.example/';
    _session = makeSession();
});

const PRIVATE = 'http://169.254.169.254/';

describe('cross-pod *At helpers refuse a private-host target before fetching', () => {
    it('podReadRoomDescriptorAt returns null and issues no fetch', async () => {
        expect(await podReadRoomDescriptorAt(PRIVATE, 'room1')).toBe(null);
        expect(_session.fetch).not.toHaveBeenCalled();
    });
    it('podReadChatDayAt returns [] and issues no fetch', async () => {
        expect(await podReadChatDayAt(PRIVATE + 'chat/', new Date())).toEqual([]);
        expect(_session.fetch).not.toHaveBeenCalled();
    });
    it('podReadChatRecentAt returns [] and issues no fetch', async () => {
        expect(await podReadChatRecentAt(PRIVATE + 'chat/')).toEqual([]);
        expect(_session.fetch).not.toHaveBeenCalled();
    });
    it('podWriteChatMessageAt returns false and issues no fetch', async () => {
        expect(await podWriteChatMessageAt(PRIVATE + 'chat/', 'm1', { content: 'x' })).toBe(false);
        expect(_session.fetch).not.toHaveBeenCalled();
    });
});

describe('a legitimate public owner pod still joins', () => {
    it('podReadRoomDescriptorAt reaches fetch and parses the descriptor', async () => {
        _session = makeSession(async () => ({
            ok: true, status: 200,
            text: async () => JSON.stringify({ room_id: 'room1', owner: 'https://alice.pod.example/profile/card#me', title: 'General' }),
        }));
        const desc = await podReadRoomDescriptorAt('https://alice.pod.example/', 'room1');
        expect(_session.fetch).toHaveBeenCalledTimes(1);
        expect(desc).toMatchObject({ room_id: 'room1', title: 'General' });
    });
});

describe('peer fetches never follow a cross-origin redirect', () => {
    // Emulate the browser refusing to follow a redirect under redirect:'error':
    // the fetch rejects instead of resolving to the private-host response.
    function redirectingSession() {
        return makeSession(async (url, opts) => {
            if (opts.redirect === 'error') throw new TypeError('Failed to fetch: redirect');
            return { ok: true, status: 200, json: async () => ({ status: 'online', signer: 'did:key:z6Mk' }) };
        });
    }

    it('podReadPresence passes redirect:error and yields null on a 302', async () => {
        _session = redirectingSession();
        expect(await podReadPresence('https://alice.pod.example/')).toBe(null);
        expect(_calls[0].opts.redirect).toBe('error');
    });
    it('podFetchPeerSigner passes redirect:error and yields null on a 302', async () => {
        _session = redirectingSession();
        expect(await podFetchPeerSigner('https://alice.pod.example/')).toBe(null);
        expect(_calls[0].opts.redirect).toBe('error');
    });
    it('podDropDm passes redirect:error and reports failure on a 302', async () => {
        _session = redirectingSession();
        expect(await podDropDm('https://alice.pod.example/', { message_id: 'm' })).toBe(false);
        expect(_calls[0].opts.redirect).toBe('error');
    });
});

describe('own-pod file helpers reject an unsafe roomId/messageId (no fetch)', () => {
    const BAD = '../../etc';   // path-traversal, fails /^[\w-]{1,128}$/
    const OK = 'room1';

    it('podUploadVoiceAudio returns null and issues no fetch for a bad roomId', async () => {
        expect(await podUploadVoiceAudio(BAD, OK, new Blob())).toBe(null);
        expect(_session.fetch).not.toHaveBeenCalled();
    });
    it('podUploadVoiceAudio returns null and issues no fetch for a bad messageId', async () => {
        expect(await podUploadVoiceAudio(OK, BAD, new Blob())).toBe(null);
        expect(_session.fetch).not.toHaveBeenCalled();
    });
    it('podDeleteVoiceAudio is a no-op (no fetch) for an unsafe id', async () => {
        await podDeleteVoiceAudio(BAD, OK);
        expect(_session.fetch).not.toHaveBeenCalled();
    });
    it('podUploadFile returns null and issues no fetch for an unsafe id', async () => {
        expect(await podUploadFile(OK, BAD, 'clip.png', new Blob())).toBe(null);
        expect(_session.fetch).not.toHaveBeenCalled();
    });
    it('a safe roomId/messageId still reaches fetch', async () => {
        expect(await podUploadVoiceAudio(OK, 'msg1', new Blob())).toBe('https://me.pod.example/proxion/rooms/room1/files/msg1.webm');
        expect(_session.fetch).toHaveBeenCalledTimes(1);
    });
});
