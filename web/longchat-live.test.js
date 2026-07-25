/**
 * Long Chat interop against a LIVE Solid server (PLAN_ROUND_67, phases A-C).
 *
 * The unit tests prove we emit what the spec describes. They cannot prove a real
 * server accepts it. These are the post-conditions that actually gate the round:
 *
 *   A  the standard terms survive a write/read round trip on a real pod
 *   B  the container layout is created, and the server ACCEPTS our
 *      SPARQL-Update PATCH (append, not clobber) at the UTC day path
 *   C  the server content-negotiates JSON-LD, and we can read back both our own
 *      chat AND a foreign one written the way SolidOS writes it (relative IRIs)
 *
 * Run (skipped entirely when TEST_CSS_CLIENT_ID is unset):
 *
 *   docker compose -f docker-compose.test.yml up -d css-alice
 *   python scripts/provision_test_pod.py          # exports TEST_CSS_* env vars
 *   cd web && npx vitest run longchat-live.test.js
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';

let _session = null;
let _storageRoot = null;

vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _storageRoot,
}));

import {
    podWriteMessageJsonLd,
    podWriteLongChatMessage,
    podReadLongChatDay,
    podReadLongChatRecent,
    ensureProxionContainer,
} from './pod.js';
import { chatIndexUrl, chatDayUrl, chatChannelIri, P } from './longchat.js';

// A fixed UTC instant so the day partition is deterministic: 2026/07/22.
const TS = '2026-07-22T10:00:00.000Z';
const DAY = '2026/07/22';

const ROOM = `lc-${Math.random().toString(36).slice(2, 10)}`;
const FOREIGN_ROOM = `lcf-${Math.random().toString(36).slice(2, 10)}`;

// Reported as skipped, not passed, when there is no pod: a test that returns
// early still counts as green, which is how a suite quietly stops testing.
const LIVE = !!process.env.TEST_CSS_CLIENT_ID;
function live() {
    return LIVE;
}
function webId() {
    return process.env.TEST_WEBID || (_session && _session.info && _session.info.webId) || '';
}

async function readAs(url, accept) {
    const res = await _session.fetch(url, { headers: { Accept: accept } });
    return { ok: res.ok, status: res.status, text: res.ok ? await res.text() : '' };
}

beforeAll(async () => {
    if (!LIVE) return;   // nothing to set up when the whole suite is skipped
    const { Session } = await import('@inrupt/solid-client-authn-node');
    _session = new Session();
    await _session.login({
        clientId: process.env.TEST_CSS_CLIENT_ID,
        clientSecret: process.env.TEST_CSS_CLIENT_SECRET,
        oidcIssuer: process.env.TEST_CSS_ISSUER,
    });
    _storageRoot = process.env.TEST_STORAGE_ROOT;
    await ensureProxionContainer();
}, 60000);

afterAll(async () => {
    if (!live() || !_session) return;
    // Best-effort cleanup: children before containers.
    for (const room of [ROOM, FOREIGN_ROOM]) {
        const targets = [
            chatDayUrl(_storageRoot, room, TS),
            `${_storageRoot}proxion/rooms/${room}/${DAY}/`,
            `${_storageRoot}proxion/rooms/${room}/2026/07/`,
            `${_storageRoot}proxion/rooms/${room}/2026/`,
            chatIndexUrl(_storageRoot, room),
        ];
        for (const url of targets) {
            try { await _session.fetch(url, { method: 'DELETE' }); } catch { /* ignore */ }
        }
    }
    await _session.logout();
}, 60000);

// ── Phase A: the standard terms survive a real round trip ───────────────────

describe.skipIf(!LIVE)('A: standard vocabulary round-trips on a live pod', () => {
    it('writes a room message whose standard terms read back correctly', async () => {
        const msg = {
            content: 'Morning, everyone',
            from_webid: webId(),
            from_display_name: 'Alice',
            timestamp: TS,
        };
        await podWriteMessageJsonLd(ROOM, 'm-a1', msg, true);

        const url = `${_storageRoot}proxion/rooms/${ROOM}/messages/m-a1.jsonld`;
        const { ok, text } = await readAs(url, 'application/ld+json');
        expect(ok).toBe(true);
        const doc = JSON.parse(text);
        const flat = JSON.stringify(doc);
        // The predicates are present after a real server stored and returned it.
        expect(flat).toContain('sioc');
        expect(flat).toContain(msg.content);
        expect(flat).toContain(webId());
    }, 60000);
});

// ── Phase B: the container layout, and PATCH actually being accepted ────────

describe.skipIf(!LIVE)('B: Long Chat container layout on a live pod', () => {
    it('creates index.ttl declaring meeting:LongChat', async () => {
        await podWriteLongChatMessage(ROOM, 'm-b1', {
            content: 'first message', from_webid: webId(), timestamp: TS,
        });
        const { ok, text } = await readAs(chatIndexUrl(_storageRoot, ROOM), 'text/turtle');
        expect(ok).toBe(true);
        expect(text).toContain('LongChat');
    }, 60000);

    it('THE gate: the server accepts our SPARQL-Update PATCH at the UTC day path', async () => {
        // podWriteLongChatMessage returns the PATCH result, so a false here means
        // the server rejected our update and the whole layout approach is wrong.
        const ok = await podWriteLongChatMessage(ROOM, 'm-b2', {
            content: 'second message', from_webid: webId(), timestamp: TS,
        });
        expect(ok).toBe(true);

        const dayUrl = chatDayUrl(_storageRoot, ROOM, TS);
        expect(dayUrl).toBe(`${_storageRoot}proxion/rooms/${ROOM}/${DAY}/chat.ttl`);
        const { ok: readOk, text } = await readAs(dayUrl, 'text/turtle');
        expect(readOk).toBe(true);
        expect(text).toContain('second message');
    }, 60000);

    it('appends rather than clobbers: earlier messages survive a later write', async () => {
        const { text } = await readAs(chatDayUrl(_storageRoot, ROOM, TS), 'text/turtle');
        // Both writes above targeted the same UTC day file.
        expect(text).toContain('first message');
        expect(text).toContain('second message');
    }, 60000);

    it('links each message to the channel with meeting:message', async () => {
        const { text } = await readAs(chatDayUrl(_storageRoot, ROOM, TS), 'text/turtle');
        // Turtle may be prefixed or absolute depending on the server's serializer,
        // so accept either form of the predicate and of the channel IRI.
        const hasPredicate = text.includes(P.message) || /meeting:message|:message\b/.test(text);
        expect(hasPredicate).toBe(true);
        const channel = chatChannelIri(_storageRoot, ROOM);
        expect(text.includes(channel) || text.includes('index.ttl#this')).toBe(true);
    }, 60000);

    it('round-trips text that would break a naive serializer', async () => {
        const nasty = 'quote " backslash \\ newline\nend " . <https://evil.example/#e> <p> "x';
        const ok = await podWriteLongChatMessage(ROOM, 'm-b3', {
            content: nasty, from_webid: webId(), timestamp: TS,
        });
        expect(ok).toBe(true);
        const msgs = await podReadLongChatDay(ROOM, TS);
        const found = msgs.find(m => m.message_id === 'm-b3');
        expect(found).toBeTruthy();
        // The exact text comes back, and no injected subject appeared.
        expect(found.content).toBe(nasty);
        expect(msgs.some(m => m.message_id.includes('evil'))).toBe(false);
    }, 60000);
});

// ── Phase C: reading back, including a foreign SolidOS-style chat ───────────

describe.skipIf(!LIVE)('C: reading a Long Chat from a live pod', () => {
    it('THE gate: the server content-negotiates JSON-LD for a .ttl resource', async () => {
        const { ok, text } = await readAs(chatDayUrl(_storageRoot, ROOM, TS), 'application/ld+json');
        expect(ok).toBe(true);
        expect(() => JSON.parse(text)).not.toThrow();
    }, 60000);

    it('reads our own messages back with content, author and timestamp intact', async () => {
        const msgs = await podReadLongChatDay(ROOM, TS);
        const first = msgs.find(m => m.content === 'first message');
        expect(first).toBeTruthy();
        expect(first.from_webid).toBe(webId());
        expect(String(first.timestamp)).toContain('2026-07-22');
        expect(first.thread_id).toBe(ROOM);
    }, 60000);

    it('returns messages oldest-first across the recent-days window', async () => {
        const msgs = await podReadLongChatRecent(ROOM, 2);
        const stamps = msgs.map(m => String(m.timestamp || ''));
        expect([...stamps].sort()).toEqual(stamps);
    }, 60000);

    it('THE interop gate: reads a foreign chat written the way SolidOS writes it', async () => {
        // Hand-written exactly like the spec example: RELATIVE IRIs and prefixed
        // names, which is what SolidOS/POD-CHAT produce. Our own writer uses
        // absolute IRIs, so this is the check that we are not merely able to read
        // our own output.
        const dayUrl = chatDayUrl(_storageRoot, FOREIGN_ROOM, TS);
        const turtle = [
            '@prefix : <#>.',
            '@prefix meeting: <http://www.w3.org/ns/pim/meeting#>.',
            '@prefix dct: <http://purl.org/dc/terms/>.',
            '@prefix sioc: <http://rdfs.org/sioc/ns#>.',
            '@prefix foaf: <http://xmlns.com/foaf/0.1/>.',
            '@prefix xsd: <http://www.w3.org/2001/XMLSchema#>.',
            '',
            '<../../../index.ttl#this> meeting:message :msgForeign .',
            '',
            ':msgForeign',
            `    dct:created "${TS}"^^xsd:dateTime;`,
            '    sioc:content """written by another app""";',
            `    foaf:maker <${webId()}>.`,
            '',
        ].join('\n');

        const put = await _session.fetch(dayUrl, {
            method: 'PUT',
            headers: { 'Content-Type': 'text/turtle' },
            body: turtle,
        });
        expect(put.ok).toBe(true);

        const msgs = await podReadLongChatDay(FOREIGN_ROOM, TS);
        const foreign = msgs.find(m => m.content === 'written by another app');
        expect(foreign).toBeTruthy();
        expect(foreign.from_webid).toBe(webId());
        expect(foreign.message_id).toBe('msgForeign');
    }, 60000);
});
