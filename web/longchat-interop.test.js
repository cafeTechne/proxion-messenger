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

import { podWriteMessageJsonLd, applyLongChatTerms, LONGCHAT_CONTEXT } from './pod.js';

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

function lastBody() {
    return JSON.parse(_calls[_calls.length - 1].body);
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
