// inbox-cap.test.js — R80 A1: the public-Append inbox must not let a flood of
// notifications drive an unbounded number of fetches per read.
import { describe, it, expect, vi, beforeEach } from 'vitest';

let _session = null;
vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => 'https://me.pod/',
}));

import { podReadInboxNotifications } from './pod.js';

const ME = 'https://me.pod/profile/card#me';
const INBOX = 'https://me.pod/inbox/';
const AS = 'https://www.w3.org/ns/activitystreams#';
const LDP = 'http://www.w3.org/ns/ldp#';

// A notification body that parses as a chat invite.
const invite = (n) => ({
    ok: true, json: async () => ({
        [AS + 'actor']: [{ '@id': 'https://a.pod/#me' }],
        [AS + 'object']: [{ [AS + 'href']: [{ '@id': `https://a.pod/room${n}/` }], [AS + 'name']: [{ '@value': 'R' }] }],
    }),
});

describe('podReadInboxNotifications flood cap', () => {
    let fetched;
    beforeEach(() => {
        fetched = 0;
        // Inbox listing with 250 contained notifications.
        const contains = Array.from({ length: 250 }, (_, i) => ({ '@id': `${INBOX}n${i}` }));
        _session = {
            info: { isLoggedIn: true, webId: ME },
            fetch: vi.fn(async (url) => {
                url = String(url);
                if (url === ME) return { ok: true, json: async () => ({ '@id': ME, [LDP + 'inbox']: [{ '@id': INBOX }] }) };
                if (url === INBOX) return { ok: true, json: async () => ({ '@id': INBOX, [LDP + 'contains']: contains }) };
                fetched++;                         // a per-notification GET
                return invite(url.slice(-3));
            }),
        };
    });

    it('processes at most 100 notifications regardless of inbox size', async () => {
        const out = await podReadInboxNotifications();
        expect(fetched).toBeLessThanOrEqual(100);
        expect(out.length).toBeLessThanOrEqual(100);
        expect(out.length).toBeGreaterThan(0);
    });
});
