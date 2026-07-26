// longchat-visible.test.js — PLAN_ROUND_68 Phase A: a Proxion room shows
// messages another Solid app (SolidOS / POD-CHAT) wrote into the same pod
// container. Exercises the read half (`podReadLongChatRecent` over its bounded
// UTC-day window) against a FOREIGN Long Chat day file, then the known-set dedup
// `loadRoomHistory` applies, proving foreign ids surface while our own dedup.
import { describe, it, expect, vi, beforeEach } from 'vitest';

let _session = null;
let _root = null;
let _calls = [];

vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _root,
}));

import { podReadLongChatRecent } from './pod.js';

const ROOT = 'https://me.pod.example/';
const ME = 'https://me.pod.example/profile/card#me';
const OTHER = 'https://friend.pod.example/profile/card#me';

// A day file as a foreign Solid app would write it: one message we authored
// (same id our px: write used) and one authored elsewhere with an id we have
// never seen. Shaped like expanded JSON-LD from a Solid server.
function foreignDay(container) {
    return [
        {
            '@id': `${container}#mine-1`,
            'http://rdfs.org/sioc/ns#content': [{ '@value': 'ours, already local' }],
            'http://purl.org/dc/terms/created': [
                { '@value': '2026-07-25T09:00:00.000Z', '@type': 'http://www.w3.org/2001/XMLSchema#dateTime' },
            ],
            'http://xmlns.com/foaf/0.1/maker': [{ '@id': ME }],
        },
        {
            '@id': `${container}#foreign-9`,
            'http://rdfs.org/sioc/ns#content': [{ '@value': 'hello from SolidOS' }],
            'http://purl.org/dc/terms/created': [
                { '@value': '2026-07-25T09:05:00.000Z', '@type': 'http://www.w3.org/2001/XMLSchema#dateTime' },
            ],
            'http://xmlns.com/foaf/0.1/maker': [{ '@id': OTHER }],
        },
    ];
}

beforeEach(() => {
    _calls = [];
    _root = ROOT;
    _session = {
        info: { isLoggedIn: true, webId: ME },
        // Return the same foreign doc for every day file the recent window walks;
        // the reader dedups by message_id across days, so it collapses to two.
        fetch: vi.fn(async (url, opts = {}) => {
            _calls.push({ url, method: opts.method || 'GET' });
            if (String(url).endsWith('/chat.ttl')) {
                const container = String(url);
                return { ok: true, status: 200, json: async () => foreignDay(container) };
            }
            return { ok: true, status: 200, json: async () => [] };
        }),
    };
});

// Mirrors loadRoomHistory's merge: seed `known` with what is already in the
// feed, inject only pod messages whose id is new.
function mergeIntoFeed(feed, podMsgs) {
    const known = new Set(feed.map(m => m.message_id));
    for (const m of podMsgs) {
        if (!m || !m.message_id || known.has(m.message_id)) continue;
        known.add(m.message_id);
        feed.push(m);
    }
    return feed;
}

describe('Phase A: foreign Long Chat messages surface in a room', () => {
    it('reads a foreign day file into Proxion messages over the recent window', async () => {
        const msgs = await podReadLongChatRecent('general', 7);
        // Two distinct ids despite the same doc being served for each day.
        expect(msgs.map(m => m.message_id).sort()).toEqual(['foreign-9', 'mine-1']);
        const foreign = msgs.find(m => m.message_id === 'foreign-9');
        expect(foreign.content).toBe('hello from SolidOS');
        expect(foreign.from_webid).toBe(OTHER);
        expect(foreign.thread_id).toBe('general');
    });

    it('injects the foreign message but dedups the one we already have locally', async () => {
        // The feed already holds our own message (written via the px: path).
        const feed = [{ message_id: 'mine-1', content: 'ours, already local', from_webid: ME }];
        const podMsgs = await podReadLongChatRecent('general', 7);
        mergeIntoFeed(feed, podMsgs);

        const ids = feed.map(m => m.message_id).sort();
        expect(ids).toEqual(['foreign-9', 'mine-1']);   // foreign added, no duplicate of ours
        expect(feed.filter(m => m.message_id === 'mine-1')).toHaveLength(1);
        expect(feed.find(m => m.message_id === 'foreign-9').content).toBe('hello from SolidOS');
    });

    it('reads a bounded window (no unbounded container crawl)', async () => {
        await podReadLongChatRecent('general', 7);
        const dayReads = _calls.filter(c => String(c.url).endsWith('/chat.ttl'));
        expect(dayReads.length).toBe(7);   // exactly the requested window, one per day
    });
});
