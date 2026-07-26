// longchat-interop.test.js — PLAN_ROUND_67 Phase A: room messages also carry the
// standard Solid chat vocabulary (SolidOS Long Chat / POD-CHAT) so other Solid
// apps can read them. Pure mock (no live CSS): a fake solidSession records the
// PUT body so we can assert the emitted RDF terms.
import { describe, it, expect, vi, beforeEach } from 'vitest';

let _session = null;
let _root = null;
let _calls = [];

vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => _root,
}));

import {
    podWriteMessageJsonLd, applyLongChatTerms, LONGCHAT_CONTEXT,
    podWriteLongChatMessage, podReadLongChatDay,
} from './pod.js';

const ROOT = 'https://alice.pod.example/';
const ALICE = 'https://alice.pod.example/profile/card#me';

function makeSession() {
    return {
        info: { isLoggedIn: true, webId: ALICE },
        fetch: vi.fn(async (url, opts = {}) => {
            _calls.push({ url, method: opts.method || 'GET', body: opts.body });
            return { ok: true, status: 200, json: async () => ({}), text: async () => '{}' };
        }),
    };
}

const MSG = {
    content: 'Morning, everyone',
    content_type: 'text',
    from_webid: ALICE,
    from_display_name: 'Alice',
    timestamp: '2026-07-22T14:03:11.000Z',
};

// Room writes now make several requests (the px: JSON-LD PUT, plus the Long
// Chat index + day-file PATCH), so target the JSON-LD message document rather
// than assuming it is the last call.
function lastBody() {
    const call = [..._calls].reverse().find(
        c => c.method === 'PUT' && String(c.url).endsWith('.jsonld')
    );
    if (!call) throw new Error('no JSON-LD message PUT was made');
    return JSON.parse(call.body);
}
function callsTo(pred) {
    return _calls.filter(pred);
}

beforeEach(() => {
    _calls = [];
    _root = ROOT;
    _session = makeSession();
});

describe('room messages carry the standard Long Chat vocabulary', () => {
    it('emits sioc:content, foaf:maker and dct:created alongside the px: terms', async () => {
        await podWriteMessageJsonLd('general', 'm-abc123', MSG, /* isRoom */ true);
        const doc = lastBody();

        // Standard terms, readable by SolidOS / POD-CHAT
        expect(doc['sioc:content']).toBe('Morning, everyone');
        expect(doc['foaf:maker']).toEqual({ '@id': ALICE });
        expect(doc['dct:created']).toEqual({
            '@value': '2026-07-22T14:03:11.000Z',
            '@type': 'http://www.w3.org/2001/XMLSchema#dateTime',
        });

        // Namespaces are declared so the document is self-describing
        expect(doc['@context'].sioc).toBe('http://rdfs.org/sioc/ns#');
        expect(doc['@context'].dct).toBe('http://purl.org/dc/terms/');
        expect(doc['@context'].foaf).toBe('http://xmlns.com/foaf/0.1/');

        // px: terms are still present — nothing is lost, the vocab is additive
        expect(doc['px:content']).toBe('Morning, everyone');
        expect(doc['px:messageId']).toBe('m-abc123');
        expect(doc['@context'].px).toBe('https://proxion.dev/vocab/v1#');
    });

    it('foaf:maker is an IRI node, never a plain string literal', async () => {
        await podWriteMessageJsonLd('general', 'm-1', MSG, true);
        const maker = lastBody()['foaf:maker'];
        expect(typeof maker).toBe('object');
        expect(maker['@id']).toBe(ALICE);
    });

    it('falls back to a generated timestamp that both vocabularies agree on', async () => {
        const { timestamp, ...noTs } = MSG;   // eslint-disable-line no-unused-vars
        await podWriteMessageJsonLd('general', 'm-2', noTs, true);
        const doc = lastBody();
        expect(doc['dct:created']['@value']).toBe(doc['px:timestamp']);
    });
});

describe('podWriteMessageJsonLd reports success (D2 write-through)', () => {
    it('returns true when the pod accepts the write', async () => {
        const ok = await podWriteMessageJsonLd('general', 'm-ok', MSG, true);
        expect(ok).toBe(true);
    });

    it('returns false when the px: message PUT is rejected', async () => {
        _session.fetch = vi.fn(async (url, opts = {}) => {
            _calls.push({ url, method: opts.method || 'GET', body: opts.body });
            // The canonical per-message PUT is denied (e.g. 403); everything else ok.
            if ((opts.method || 'GET') === 'PUT' && String(url).endsWith('.jsonld')) {
                return { ok: false, status: 403 };
            }
            return { ok: true, status: 200, json: async () => ({}), text: async () => '' };
        });
        const ok = await podWriteMessageJsonLd('general', 'm-bad', MSG, true);
        expect(ok).toBe(false);
    });

    it('returns false for a room when the Long Chat PATCH is rejected', async () => {
        _session.fetch = vi.fn(async (url, opts = {}) => {
            _calls.push({ url, method: opts.method || 'GET', body: opts.body });
            if ((opts.method || 'GET') === 'PATCH') return { ok: false, status: 409 };
            return { ok: true, status: 200, json: async () => ({}), text: async () => '' };
        });
        const ok = await podWriteMessageJsonLd('general', 'm-lc', MSG, true);
        expect(ok).toBe(false);
    });
});

describe('DMs stay px:-only (E2E cannot be third-party readable)', () => {
    it('does NOT emit Long Chat terms for a direct message', async () => {
        await podWriteMessageJsonLd('thread-1', 'm-dm', MSG, /* isRoom */ false);
        const doc = lastBody();
        expect(doc['sioc:content']).toBeUndefined();
        expect(doc['foaf:maker']).toBeUndefined();
        expect(doc['dct:created']).toBeUndefined();
        expect(doc['@context'].sioc).toBeUndefined();
        // still a well-formed px: message
        expect(doc['px:content']).toBe('Morning, everyone');
    });
});

describe('applyLongChatTerms (pure)', () => {
    it('is additive and does not drop existing context or terms', () => {
        const doc = { '@context': { px: 'https://proxion.dev/vocab/v1#' }, 'px:content': 'hi' };
        applyLongChatTerms(doc, { content: 'hi', from_webid: ALICE }, '2026-01-01T00:00:00.000Z');
        expect(doc['@context'].px).toBe('https://proxion.dev/vocab/v1#');
        expect(doc['px:content']).toBe('hi');
        expect(doc['sioc:content']).toBe('hi');
    });

    it('omits foaf:maker when there is no identity rather than emitting an empty IRI', () => {
        const doc = { '@context': {} };
        applyLongChatTerms(doc, { content: 'x' }, '2026-01-01T00:00:00.000Z');
        expect(doc['foaf:maker']).toBeUndefined();
        expect(doc['sioc:content']).toBe('x');
    });

    it('accepts a did:key maker as an IRI (pod-less identity, not dereferenceable)', () => {
        const doc = { '@context': {} };
        const did = 'did:key:z6MkExample';
        applyLongChatTerms(doc, { content: 'x', from_webid: did }, '2026-01-01T00:00:00.000Z');
        expect(doc['foaf:maker']).toEqual({ '@id': did });
    });

    it('exports the namespace map it applies', () => {
        expect(LONGCHAT_CONTEXT.sioc).toBe('http://rdfs.org/sioc/ns#');
        expect(Object.isFrozen(LONGCHAT_CONTEXT)).toBe(true);
    });
});

// ── Phase B: the Long Chat container layout ─────────────────────────────────

describe('Long Chat container layout (Phase B)', () => {
    it('writes the channel index and appends the message to its UTC day file', async () => {
        await podWriteLongChatMessage('general', 'm-b1', MSG);

        const patch = callsTo(c => c.method === 'PATCH')[0];
        expect(patch).toBeTruthy();
        // Date partition comes from the message's UTC date, per the spec.
        expect(patch.url).toBe(`${ROOT}proxion/rooms/general/2026/07/22/chat.ttl`);

        // The link predicate is meeting:message, not wf:message.
        expect(patch.body).toContain('<http://www.w3.org/ns/pim/meeting#message>');
        expect(patch.body).toContain('<http://rdfs.org/sioc/ns#content>');
        expect(patch.body).toContain('<http://purl.org/dc/terms/created>');
        expect(patch.body).toContain('<http://xmlns.com/foaf/0.1/maker>');
        // Channel is the subject of the link triple.
        expect(patch.body).toContain(`<${ROOT}proxion/rooms/general/index.ttl#this>`);
        expect(patch.body.startsWith('INSERT DATA {')).toBe(true);
    });

    it('creates the channel index as meeting:LongChat when absent', async () => {
        _session.fetch = vi.fn(async (url, opts = {}) => {
            _calls.push({ url, method: opts.method || 'GET', body: opts.body });
            // Report the index as missing so the writer creates it.
            if ((opts.method || 'GET') === 'HEAD') return { ok: false, status: 404 };
            return { ok: true, status: 200, json: async () => ({}), text: async () => '' };
        });
        await podWriteLongChatMessage('general', 'm-b2', MSG);
        const put = callsTo(c => c.method === 'PUT' && String(c.url).endsWith('index.ttl'))[0];
        expect(put).toBeTruthy();
        expect(put.body).toContain('a meeting:LongChat');
        expect(put.body).toContain('http://www.w3.org/ns/pim/meeting#');
        // Titles use Dublin Core ELEMENTS, not TERMS.
        expect(put.body).toContain('http://purl.org/dc/elements/1.1/');
    });

    it('a room message write also produces the Long Chat layout', async () => {
        await podWriteMessageJsonLd('general', 'm-b3', MSG, true);
        expect(callsTo(c => c.method === 'PATCH').length).toBe(1);
        expect(lastBody()['sioc:content']).toBe('Morning, everyone');   // px: doc still written
    });

    it('a DM write produces no Long Chat layout at all', async () => {
        await podWriteMessageJsonLd('thread-1', 'm-b4', MSG, false);
        expect(callsTo(c => c.method === 'PATCH')).toHaveLength(0);
        expect(callsTo(c => String(c.url).includes('index.ttl'))).toHaveLength(0);
    });
});

// ── Phase C: reading a Long Chat back ───────────────────────────────────────

describe('reading a Long Chat (Phase C)', () => {
    // Shaped like expanded JSON-LD from a Solid server, as SolidOS would write.
    const DAY_DOC = [
        {
            '@id': `${ROOT}proxion/rooms/general/2026/07/22/chat.ttl#msg1`,
            'http://rdfs.org/sioc/ns#content': [{ '@value': 'from SolidOS' }],
            'http://purl.org/dc/terms/created': [
                { '@value': '2026-07-22T10:00:00Z', '@type': 'http://www.w3.org/2001/XMLSchema#dateTime' },
            ],
            'http://xmlns.com/foaf/0.1/maker': [{ '@id': ALICE }],
        },
        {
            '@id': `${ROOT}proxion/rooms/general/index.ttl#this`,
            'http://www.w3.org/ns/pim/meeting#message': [
                { '@id': `${ROOT}proxion/rooms/general/2026/07/22/chat.ttl#msg1` },
            ],
        },
    ];

    it('parses a foreign (SolidOS-written) day file into Proxion messages', async () => {
        _session.fetch = vi.fn(async (url, opts = {}) => {
            _calls.push({ url, method: opts.method || 'GET', body: opts.body });
            return { ok: true, status: 200, json: async () => DAY_DOC };
        });
        const msgs = await podReadLongChatDay('general', '2026-07-22T00:00:00Z');
        expect(msgs).toHaveLength(1);          // the channel node is not a message
        expect(msgs[0].content).toBe('from SolidOS');
        expect(msgs[0].from_webid).toBe(ALICE);
        expect(msgs[0].message_id).toBe('msg1');
        expect(msgs[0].thread_id).toBe('general');
    });

    it('requests JSON-LD so no RDF parser is needed in the browser', async () => {
        let accept = null;
        _session.fetch = vi.fn(async (url, opts = {}) => {
            accept = (opts.headers || {}).Accept;
            return { ok: true, status: 200, json: async () => [] };
        });
        await podReadLongChatDay('general', '2026-07-22T00:00:00Z');
        expect(accept).toBe('application/ld+json');
    });

    it('returns an empty list rather than throwing when the day has no file', async () => {
        _session.fetch = vi.fn(async () => ({ ok: false, status: 404 }));
        await expect(podReadLongChatDay('general', '2026-07-22T00:00:00Z')).resolves.toEqual([]);
    });
});

// The failure mode that would silently break interop is a wrong namespace IRI:
// the JSON-LD still looks correct, but expands to predicates no other Solid app
// is looking for. Expand the compact IRIs against the emitted @context and pin
// the resulting absolute predicates to the ones SolidOS / POD-CHAT actually read.
describe('emitted terms expand to the exact standard predicate IRIs', () => {
    function expand(doc, term) {
        const [prefix, local] = term.split(':');
        const ns = doc['@context'][prefix];
        if (!ns) throw new Error(`prefix "${prefix}" is not declared in @context`);
        return ns + local;
    }

    it('expands to the predicates the Solid chat ecosystem reads', async () => {
        await podWriteMessageJsonLd('general', 'm-ns', MSG, true);
        const doc = lastBody();
        expect(expand(doc, 'sioc:content')).toBe('http://rdfs.org/sioc/ns#content');
        expect(expand(doc, 'dct:created')).toBe('http://purl.org/dc/terms/created');
        expect(expand(doc, 'foaf:maker')).toBe('http://xmlns.com/foaf/0.1/maker');
    });

    it('every standard term it emits is backed by a declared prefix', async () => {
        await podWriteMessageJsonLd('general', 'm-ns2', MSG, true);
        const doc = lastBody();
        for (const key of Object.keys(doc)) {
            if (key.startsWith('@') || key.startsWith('px:')) continue;
            expect(() => expand(doc, key)).not.toThrow();
        }
    });
});
