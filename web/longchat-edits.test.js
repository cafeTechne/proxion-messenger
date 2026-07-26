// longchat-edits.test.js — PLAN_ROUND_68 Phase B: edits and deletes are
// reflected in the Long Chat view. An edit rewrites sioc:content in place; a
// delete appends a schema:dateDeleted tombstone. Pure-builder + reader unit
// tests here; the mocked pod I/O proves the right day file is PATCHed. The live
// round trip against a real CSS lives in longchat-live.test.js.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
    buildEditPatch, buildDeletePatch, parseLongChatJsonLd, P, NS,
} from './longchat.js';

const ROOT = 'https://alice.pod.example/';
const ALICE = 'https://alice.pod.example/profile/card#me';
const MSG_IRI = `${ROOT}proxion/rooms/general/2026/07/22/chat.ttl#m-1`;

describe('buildEditPatch (in-place content rewrite)', () => {
    it('replaces the existing sioc:content with DELETE/INSERT/WHERE, not DELETE DATA', () => {
        const patch = buildEditPatch({ messageIri: MSG_IRI, newContent: 'edited text' });
        expect(patch).toMatch(/^DELETE \{/);
        expect(patch).toContain('INSERT {');
        expect(patch).toContain('WHERE');
        // Content predicate on both sides, message IRI as subject.
        expect(patch).toContain(`<${MSG_IRI}> <${P.content}> ?old`);
        expect(patch).toContain(`<${MSG_IRI}> <${P.content}> "edited text"`);
        // Not the append-only form.
        expect(patch).not.toContain('INSERT DATA');
    });

    it('escapes the new text so an edit cannot inject triples', () => {
        const evil = 'x" . <https://evil.example/#e> <http://p> "y';
        const patch = buildEditPatch({ messageIri: MSG_IRI, newContent: evil });
        // The quotes that would close the literal are backslash-escaped, so the
        // hostile <...> stays inert text INSIDE the literal, never a live triple.
        expect(patch).toContain('x\\"');
        // Structural proof (not substring): the content predicate appears exactly
        // three times — DELETE ?old, INSERT literal, WHERE ?old. Injection would
        // add more. The evil IRI contributes zero live triples.
        expect(patch.match(new RegExp(P.content.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g')))
            .toHaveLength(3);
        expect(patch.match(/INSERT \{/g)).toHaveLength(1);
        expect(patch.match(/DELETE \{/g)).toHaveLength(1);
    });
});

describe('buildDeletePatch (soft-delete tombstone)', () => {
    it('appends a schema:dateDeleted typed as xsd:dateTime', () => {
        const patch = buildDeletePatch({ messageIri: MSG_IRI, deletedIso: '2026-07-25T12:00:00.000Z' });
        expect(patch).toContain('INSERT DATA {');
        expect(patch).toContain(`<${MSG_IRI}> <${P.dateDeleted}>`);
        expect(patch).toContain('"2026-07-25T12:00:00.000Z"^^<' + P.dateTime + '>');
        // schema.org, the standard tombstone term.
        expect(P.dateDeleted).toBe('http://schema.org/dateDeleted');
    });

    it('keeps the node (does not DELETE content) so the day file stays valid', () => {
        const patch = buildDeletePatch({ messageIri: MSG_IRI, deletedIso: '2026-07-25T12:00:00.000Z' });
        expect(patch).not.toContain('DELETE');
    });
});

describe('parseLongChatJsonLd honours a tombstone on read', () => {
    const day = (extra) => [{
        '@id': MSG_IRI,
        [P.content]: [{ '@value': 'the original text' }],
        [P.created]: [{ '@value': '2026-07-22T10:00:00.000Z', '@type': P.dateTime }],
        [P.maker]: [{ '@id': ALICE }],
        ...extra,
    }];

    it('a live message reads normally', () => {
        const [m] = parseLongChatJsonLd(day({}), 'general');
        expect(m.deleted).toBe(false);
        expect(m.content).toBe('the original text');
    });

    it('a schema:dateDeleted message reads as deleted with no content', () => {
        const [m] = parseLongChatJsonLd(
            day({ [P.dateDeleted]: [{ '@value': '2026-07-25T12:00:00.000Z', '@type': P.dateTime }] }),
            'general',
        );
        expect(m.deleted).toBe(true);
        expect(m.deleted_at).toBe('2026-07-25T12:00:00.000Z');
        expect(m.content).toBe('');            // stale text never surfaces
        expect(m.message_id).toBe('m-1');
    });

    it('exposes schema in the namespace map', () => {
        expect(NS.schema).toBe('http://schema.org/');
    });
});

// ── Mocked pod I/O: the edit/delete PATCH targets the right day file ─────────

let _session = null;
let _root = null;
let _calls = [];

vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _root,
}));

import { podEditLongChatMessage, podSoftDeleteLongChatMessage } from './pod.js';

beforeEach(() => {
    _calls = [];
    _root = ROOT;
    _session = {
        info: { isLoggedIn: true, webId: ALICE },
        fetch: vi.fn(async (url, opts = {}) => {
            _calls.push({ url: String(url), method: opts.method || 'GET', body: opts.body, headers: opts.headers });
            return { ok: true, status: 205 };
        }),
    };
});

describe('podEditLongChatMessage / podSoftDeleteLongChatMessage', () => {
    const TS = '2026-07-22T10:00:00.000Z';
    const DAY_FILE = `${ROOT}proxion/rooms/general/2026/07/22/chat.ttl`;

    it('edits the message in the day file for its ORIGINAL date, as sparql-update', async () => {
        const ok = await podEditLongChatMessage('general', 'm-1', TS, 'new words');
        expect(ok).toBe(true);
        const patch = _calls.find(c => c.method === 'PATCH');
        expect(patch.url).toBe(DAY_FILE);
        expect(patch.headers['Content-Type']).toBe('application/sparql-update');
        expect(patch.body).toContain('DELETE {');
        expect(patch.body).toContain('"new words"');
    });

    it('soft-deletes by PATCHing a tombstone into the same day file', async () => {
        const ok = await podSoftDeleteLongChatMessage('general', 'm-1', TS, '2026-07-25T12:00:00.000Z');
        expect(ok).toBe(true);
        const patch = _calls.find(c => c.method === 'PATCH');
        expect(patch.url).toBe(DAY_FILE);
        expect(patch.body).toContain('schema.org/dateDeleted');
        expect(patch.body).toContain('INSERT DATA {');
    });

    it('is a no-op (no throw, false) when logged out', async () => {
        _session.info.isLoggedIn = false;
        expect(await podEditLongChatMessage('general', 'm-1', TS, 'x')).toBe(false);
        expect(await podSoftDeleteLongChatMessage('general', 'm-1', TS)).toBe(false);
        expect(_calls).toHaveLength(0);
    });
});
