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
    podPublishSigner,
    podPublishIdentityAcl,
    podDropDm,
    podUploadVoiceAudio,
    podDeleteVoiceAudio,
    podUploadFile,
    podListChatsForWebId,
    podReadPublicTypeIndexUrlFor,
    podDiscoverInbox,
    podSendChatInvite,
    podReadDmDrops,
} from './pod.js';
import { isPeerPodRootAllowed } from './ssrf.js';

const CHAT_CLASS = 'http://www.w3.org/ns/pim/meeting#LongChat';

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
    it('podReadChatDayAt passes redirect:error on the day-file GET', async () => {
        _session = redirectingSession();
        expect(await podReadChatDayAt('https://alice.pod.example/OurChat/', new Date())).toEqual([]);
        expect(_calls[0].opts.redirect).toBe('error');
    });
    it('podWriteChatMessageAt sends redirect:error on every write (ensureChatIndexAt + PATCH)', async () => {
        // One call exercises ensureChatIndexAt's HEAD+PUT and the day-file HEAD+PATCH;
        // none of them may follow a redirect off the vetted host.
        _session = redirectingSession();
        expect(await podWriteChatMessageAt('https://alice.pod.example/OurChat/', 'm1', { content: 'x' })).toBe(false);
        expect(_calls.length).toBeGreaterThan(0);
        expect(_calls.every(c => c.opts.redirect === 'error')).toBe(true);
        expect(_calls.some(c => c.opts.method === 'PATCH')).toBe(true);
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

// C1: the join-approval container choice (main.js `_onWebJoinApproved`). long_chat
// rides in the approver's own room.json, so it is only honoured when it clears the
// SSRF gate AND is same-origin as the already-validated owner_pod_root; otherwise
// the safe URL derived from the owner root is used. This mirrors that exact
// predicate against the real isPeerPodRootAllowed so the decision stays locked.
describe('join-approval long_chat gate (C1)', () => {
    const OWNER = 'https://alice.pod.example/';
    const DERIVED = 'https://alice.pod.example/proxion/rooms/room1/';
    function chooseJoinContainer(longChat, ownerPodRoot, selfRoot, derived) {
        if (longChat && isPeerPodRootAllowed(longChat, selfRoot)) {
            try {
                if (new URL(longChat).origin === new URL(ownerPodRoot).origin) return longChat;
            } catch { /* malformed → derived */ }
        }
        return derived;
    }

    it('falls back to the derived URL for a private/loopback long_chat', () => {
        expect(chooseJoinContainer('http://169.254.169.254/x/', OWNER, _root, DERIVED)).toBe(DERIVED);
        expect(chooseJoinContainer('http://localhost:3000/x/', OWNER, _root, DERIVED)).toBe(DERIVED);
    });
    it('falls back for a public but cross-origin long_chat (federation would allow it, we must not)', () => {
        expect(isPeerPodRootAllowed('https://evil.example/x/', _root)).toBe(true);   // SSRF gate alone passes it
        expect(chooseJoinContainer('https://evil.example/x/', OWNER, _root, DERIVED)).toBe(DERIVED);
    });
    it('uses a same-origin long_chat', () => {
        const same = 'https://alice.pod.example/some/other/chat/';
        expect(chooseJoinContainer(same, OWNER, _root, DERIVED)).toBe(same);
    });
    it('falls back for a malformed or absent long_chat', () => {
        expect(chooseJoinContainer('not a url', OWNER, _root, DERIVED)).toBe(DERIVED);
        expect(chooseJoinContainer('', OWNER, _root, DERIVED)).toBe(DERIVED);
    });
});

// C2: chat discovery over a foreign, attacker-writable type index. The fan-out is
// capped and every container is shape/SSRF-gated before we authenticate to it.
describe('podListChatsForWebId gates and caps discovery (C2)', () => {
    const WEBID = 'https://alice.pod.example/profile/card#me';
    const INDEX = 'https://alice.pod.example/settings/publicTypeIndex.ttl';
    function discoverySession(containers) {
        return makeSession(async (url) => {
            if (url === WEBID) {
                return { ok: true, status: 200, json: async () => ({
                    '@id': WEBID,
                    'http://www.w3.org/ns/solid/terms#publicTypeIndex': [{ '@id': INDEX }],
                }) };
            }
            if (url === INDEX) {
                return { ok: true, status: 200, json: async () => ({
                    '@graph': containers.map((c, i) => ({
                        '@id': `${INDEX}#reg${i}`,
                        'http://www.w3.org/ns/solid/terms#forClass': [{ '@id': CHAT_CLASS }],
                        'http://www.w3.org/ns/solid/terms#instanceContainer': [{ '@id': c }],
                    })),
                }) };
            }
            return { ok: true, status: 200, text: async () => '' };   // _readChatTitle
        });
    }
    const isTitleFetch = (u) => u.endsWith('index.ttl');

    it('skips a private-host container and never authenticates to it', async () => {
        _session = discoverySession([
            'https://alice.pod.example/OurChat/',
            'http://169.254.169.254/evil/',
        ]);
        const out = await podListChatsForWebId(WEBID);
        expect(out.map(o => o.container)).toEqual(['https://alice.pod.example/OurChat/']);
        expect(_calls.some(c => c.url.includes('169.254.169.254'))).toBe(false);
    });

    it('skips a malformed (non-container) URL', async () => {
        _session = discoverySession([
            'https://alice.pod.example/Good/',
            'https://alice.pod.example/NoTrailingSlash',
        ]);
        const out = await podListChatsForWebId(WEBID);
        expect(out.map(o => o.container)).toEqual(['https://alice.pod.example/Good/']);
    });

    it('caps the fan-out at 100 authenticated title reads', async () => {
        const many = Array.from({ length: 150 }, (_, i) => `https://alice.pod.example/c${i}/`);
        _session = discoverySession(many);
        const out = await podListChatsForWebId(WEBID);
        expect(out.length).toBe(100);
        expect(_calls.filter(c => isTitleFetch(c.url)).length).toBe(100);
    });

    it('asks fetch not to follow a redirect when reading a title', async () => {
        _session = discoverySession(['https://alice.pod.example/OurChat/']);
        await podListChatsForWebId(WEBID);
        const titleCall = _calls.find(c => isTitleFetch(c.url));
        expect(titleCall.opts.redirect).toBe('error');
    });
});

// C3: a foreign Long Chat day file is untrusted; its body is bounded before parse.
describe('podReadChatDayAt bounds an oversized day file (C3)', () => {
    const CHAT = 'https://alice.pod.example/OurChat/';
    it('rejects a body whose Content-Length exceeds the cap', async () => {
        _session = makeSession(async () => ({
            ok: true, status: 200,
            headers: { get: () => String(600 * 1024) },
            text: async () => { throw new Error('should not read body'); },
        }));
        expect(await podReadChatDayAt(CHAT, new Date())).toEqual([]);
    });
    it('rejects a body whose text exceeds the cap when no Content-Length is sent', async () => {
        _session = makeSession(async () => ({
            ok: true, status: 200,
            text: async () => 'x'.repeat(600 * 1024),
        }));
        expect(await podReadChatDayAt(CHAT, new Date())).toEqual([]);
    });
    it('still parses a normal small day file', async () => {
        const node = {
            '@id': `${CHAT}2026/07/22.ttl#m1`,
            'http://rdfs.org/sioc/ns#content': [{ '@value': 'hi' }],
            'http://purl.org/dc/terms/created': [{ '@value': '2026-07-22T10:00:00Z' }],
        };
        _session = makeSession(async () => ({
            ok: true, status: 200,
            text: async () => JSON.stringify({ '@graph': [node] }),
        }));
        const out = await podReadChatDayAt(CHAT, new Date('2026-07-22T10:00:00Z'), 'room1');
        expect(out).toHaveLength(1);
        expect(out[0]).toMatchObject({ message_id: 'm1', content: 'hi' });
    });
});

// F8: a URL discovered inside an attacker-authored profile (its publicTypeIndex, its
// ldp:inbox) is followed with our authenticated session, so it must be same-origin as
// the profile and clear the SSRF gate. F7: the profile body is bounded before parse.
describe('foreign-profile discovery URLs are origin-gated and body-capped (F7/F8)', () => {
    const WEBID = 'https://alice.pod.example/profile/card#me';
    const TI_PRED = 'http://www.w3.org/ns/solid/terms#publicTypeIndex';
    const INBOX_PRED = 'http://www.w3.org/ns/ldp#inbox';
    function profileSession(predObj) {
        return makeSession(async (url) => {
            if (url === WEBID) {
                return { ok: true, status: 200, headers: { get: () => null },
                    text: async () => JSON.stringify({ '@id': WEBID, ...predObj }) };
            }
            return { ok: true, status: 200, text: async () => '{}' };
        });
    }

    it('accepts a same-origin publicTypeIndex', async () => {
        _session = profileSession({ [TI_PRED]: [{ '@id': 'https://alice.pod.example/settings/idx.ttl' }] });
        expect(await podReadPublicTypeIndexUrlFor(WEBID)).toBe('https://alice.pod.example/settings/idx.ttl');
    });
    it('rejects a cross-origin publicTypeIndex (SSRF)', async () => {
        _session = profileSession({ [TI_PRED]: [{ '@id': 'https://evil.example/idx.ttl' }] });
        expect(await podReadPublicTypeIndexUrlFor(WEBID)).toBe(null);
    });
    it('rejects a private-host publicTypeIndex', async () => {
        _session = profileSession({ [TI_PRED]: [{ '@id': 'http://169.254.169.254/idx.ttl' }] });
        expect(await podReadPublicTypeIndexUrlFor(WEBID)).toBe(null);
    });
    it('rejects an oversized profile body before parse (F7 byte cap)', async () => {
        _session = makeSession(async () => ({
            ok: true, status: 200,
            headers: { get: () => String(600 * 1024) },
            text: async () => { throw new Error('should not read an oversized body'); },
        }));
        expect(await podReadPublicTypeIndexUrlFor(WEBID)).toBe(null);
    });
    it('podDiscoverInbox accepts a same-origin inbox but rejects a cross-origin one', async () => {
        _session = profileSession({ [INBOX_PRED]: [{ '@id': 'https://alice.pod.example/inbox/' }] });
        expect(await podDiscoverInbox(WEBID)).toBe('https://alice.pod.example/inbox/');
        _session = profileSession({ [INBOX_PRED]: [{ '@id': 'https://evil.example/inbox/' }] });
        expect(await podDiscoverInbox(WEBID)).toBe(null);
    });
});

describe('identity trust anchors get a public-read ACL (D2 / D4)', () => {
    const SIGNER_URL = 'https://me.pod.example/proxion/identity/signer.json';

    // A tiny in-memory pod: PUT stores a body at its URL; GET returns it (so the
    // ACL read-back sees what we wrote); HEAD carries no Link, so discovery falls
    // back to the `.acl` convention + WAC (the CSS/NSS path).
    function podSession() {
        const store = {};
        return {
            info: { isLoggedIn: true, webId: 'https://me.pod.example/profile/card#me' },
            fetch: vi.fn(async (url, opts = {}) => {
                _calls.push({ url, opts });
                const method = opts.method || 'GET';
                if (method === 'PUT') { store[url] = opts.body; return { ok: true, status: 201 }; }
                if (method === 'HEAD') { return { ok: true, status: 200 }; }
                const body = store[url];
                return body != null
                    ? { ok: true, status: 200, text: async () => body, json: async () => JSON.parse(body) }
                    : { ok: false, status: 404, text: async () => '', json: async () => ({}) };
            }),
        };
    }

    it('podPublishSigner writes signer.json then a foaf:Agent acl:Read ACL on that file', async () => {
        _session = podSession();
        expect(await podPublishSigner('did:key:zSigner', 'did:key:zAccount')).toBe(true);

        const put = _calls.find((c) => c.url === SIGNER_URL && c.opts.method === 'PUT');
        expect(put).toBeTruthy();
        expect(JSON.parse(put.opts.body)).toMatchObject({ version: 1, signer: 'did:key:zSigner' });

        const aclPut = _calls.find((c) => c.url === SIGNER_URL + '.acl' && c.opts.method === 'PUT');
        expect(aclPut).toBeTruthy();
        const acl = aclPut.opts.body;
        // Public gets READ, granted to everyone (foaf:Agent), scoped to this file.
        expect(acl).toContain('acl:agentClass foaf:Agent');
        expect(acl).toContain(`acl:accessTo <${SIGNER_URL}>`);
        const publicBlock = acl.split('#public')[1] || '';
        expect(publicBlock).toContain('acl:mode acl:Read');
        // ...never write/append/control to the public.
        expect(publicBlock).not.toContain('acl:Write');
        expect(publicBlock).not.toContain('acl:Append');
        expect(publicBlock).not.toContain('acl:Control');
        // Scoped to the file only: no broad acl:default that would expose other
        // private proxion/ children.
        expect(acl).not.toContain('acl:default');
        expect(acl).not.toContain('/proxion/>');
    });

    it('reads the ACL back and reports the grant is in place (D4)', async () => {
        _session = podSession();
        expect(await podPublishIdentityAcl(SIGNER_URL)).toBe(true);
        const aclGet = _calls.find((c) => c.url === SIGNER_URL + '.acl' && (c.opts.method || 'GET') === 'GET');
        expect(aclGet).toBeTruthy();   // read-back happened
    });

    it('surfaces a failure (returns false) when the ACL PUT is refused', async () => {
        _session = makeSession(async (url, opts = {}) => {
            if (opts.method === 'PUT' && url.endsWith('.acl')) return { ok: false, status: 403 };
            return { ok: true, status: 200, text: async () => '', json: async () => ({}) };
        });
        expect(await podPublishIdentityAcl(SIGNER_URL)).toBe(false);
    });

    it('a peer-read of signer.json is not owner-gated (public GET succeeds)', async () => {
        // The peer authenticates as ITSELF, not the owner; with the public-read ACL
        // in place the server serves the file, so podFetchPeerSigner returns it.
        _session = makeSession(async () => ({
            ok: true, status: 200,
            json: async () => ({ version: 1, signer: 'did:key:zPeer', account_did: null }),
            text: async () => JSON.stringify({ version: 1, signer: 'did:key:zPeer' }),
        }));
        const got = await podFetchPeerSigner('https://alice.pod.example/');
        expect(got).toEqual({ signer: 'did:key:zPeer', account_did: null });
    });
});
