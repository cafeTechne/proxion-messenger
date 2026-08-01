// discover.test.js — PLAN_ROUND_74 F1: discover the chats a WebID hosts, via its
// public type index. Mocked session; the live cross-identity path is exercised by
// the two-identity harness (podcanonical-b4 pattern).
import { describe, it, expect, vi, beforeEach } from 'vitest';

let _session = null;
vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => 'https://me.pod/',
}));

import { podListChatsForWebId } from './pod.js';

const ALICE = 'https://alice.pod/profile/card#me';
const IDX = 'https://alice.pod/settings/publicTypeIndex.ttl';
const CONTAINER = 'https://alice.pod/proxion/rooms/team/';
const SOLID = 'http://www.w3.org/ns/solid/terms#';
const CHAT_CLASS = 'http://www.w3.org/ns/pim/meeting#LongChat';

const profile = () => ({ ok: true, json: async () => ({ '@id': ALICE, [SOLID + 'publicTypeIndex']: [{ '@id': IDX }] }) });
const index = () => ({ ok: true, json: async () => [{ '@id': IDX + '#reg', [SOLID + 'forClass']: [{ '@id': CHAT_CLASS }], [SOLID + 'instanceContainer']: [{ '@id': CONTAINER }] }] });

beforeEach(() => {
    _session = {
        info: { isLoggedIn: true, webId: 'https://me.pod/profile/card#me' },
        fetch: vi.fn(async (url) => {
            url = String(url);
            if (url === ALICE) return profile();
            if (url === IDX) return index();
            if (url === CONTAINER + 'index.ttl') return { ok: true, text: async () => '@prefix dc: <http://purl.org/dc/elements/1.1/>.\n<#this> a meeting:LongChat; dc:title "Team Chat" .' };
            return { ok: false, status: 404 };
        }),
    };
});

describe('podListChatsForWebId', () => {
    it("discovers a WebID's hosted chats with their titles", async () => {
        const chats = await podListChatsForWebId(ALICE);
        expect(chats).toHaveLength(1);
        expect(chats[0].container).toBe(CONTAINER);
        expect(chats[0].title).toBe('Team Chat');
    });

    it('falls back to the room id when the chat title is not readable', async () => {
        _session.fetch = vi.fn(async (url) => {
            url = String(url);
            if (url === ALICE) return profile();
            if (url === IDX) return index();
            return { ok: false, status: 403 };   // chat index.ttl not readable
        });
        const chats = await podListChatsForWebId(ALICE);
        expect(chats[0].title).toBe('team');      // roomId from the container path
    });

    it('returns [] when the WebID has no public type index', async () => {
        _session.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ '@id': ALICE }) }));
        expect(await podListChatsForWebId(ALICE)).toEqual([]);
    });

    it('returns [] for a falsy webid', async () => {
        expect(await podListChatsForWebId('')).toEqual([]);
    });

    it('returns [] when the profile is not readable', async () => {
        _session.fetch = vi.fn(async () => ({ ok: false, status: 401 }));
        expect(await podListChatsForWebId(ALICE)).toEqual([]);
    });
});
