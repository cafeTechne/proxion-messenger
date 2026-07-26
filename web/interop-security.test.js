// interop-security.test.js — PLAN_ROUND_70 Track C: security regression for the
// surfaces R67-R69 added, where data comes from ARBITRARY other Solid apps/users
// and now (D1) flows into the MAIN room feed, not just the textContent-safe Solid
// Chat panel. The audit found no vulnerabilities; these tests lock the properties
// so a regression fails loudly.
import { describe, it, expect } from 'vitest';
import {
    parseLongChatJsonLd, buildSeqPatch, buildDeletePatch, buildEditPatch,
    compareByOrder, P,
} from './longchat.js';
import { renderMarkdown } from './util.js';

const NS = {
    content: 'http://rdfs.org/sioc/ns#content',
    created: 'http://purl.org/dc/terms/created',
    maker: 'http://xmlns.com/foaf/0.1/maker',
    dt: 'http://www.w3.org/2001/XMLSchema#dateTime',
    px: 'https://proxion.dev/vocab/v1#',
};

// A hostile day file exactly as a malicious foreign app/user could write it.
const HOSTILE = [{
    '@id': 'https://evil.pod/c/chat.ttl#"><img src=x onerror=alert(1)>',
    [NS.content]: [{ '@value': '<script>alert(document.cookie)</script>' }],
    [NS.created]: [{ '@value': '2026-07-26T09:00:00.000Z', '@type': NS.dt }],
    [NS.maker]: [{ '@id': 'https://evil.pod/profile/card#me' }],
    [NS.px + 'fromName']: [{ '@value': '<b>spoofed</b>' }],
    [NS.px + 'seq']: [{ '@value': 'not-a-number' }],
}];

describe('parseLongChatJsonLd treats foreign data as inert', () => {
    it('returns hostile content as a plain string, never executes or transforms it', () => {
        const [m] = parseLongChatJsonLd(HOSTILE, 'general');
        expect(m.content).toBe('<script>alert(document.cookie)</script>');   // data, not markup
        expect(m.from_display_name).toBe('<b>spoofed</b>');                   // data, escaped at render
    });

    it('ignores a non-numeric px:seq rather than poisoning the order', () => {
        const [m] = parseLongChatJsonLd(HOSTILE, 'general');
        expect('seq' in m).toBe(false);   // NaN is dropped; falls back to timestamp order
    });

    it('does not let a hostile @id fragment break id extraction', () => {
        const [m] = parseLongChatJsonLd(HOSTILE, 'general');
        // The fragment after the last # is taken verbatim as an opaque id; it is
        // only ever used as a Map key / dedup token, never interpolated into RDF.
        expect(typeof m.message_id).toBe('string');
        expect(m.thread_id).toBe('general');
    });
});

describe('the D1 path: foreign content is escaped when rendered into the main feed', () => {
    it('renderMarkdown neutralises hostile foreign message content', () => {
        const [m] = parseLongChatJsonLd(HOSTILE, 'general');
        const html = renderMarkdown(m.content);
        expect(html).not.toContain('<script');
        expect(html).toContain('&lt;script&gt;');
    });

    it('renderMarkdown neutralises a hostile foreign display name', () => {
        const [m] = parseLongChatJsonLd(HOSTILE, 'general');
        expect(renderMarkdown(m.from_display_name)).toBe('&lt;b&gt;x&lt;/b&gt;'.replace('x', 'spoofed'));
    });
});

describe('D4 seq surface cannot be used to inject Turtle', () => {
    it('buildSeqPatch only ever emits an integer, never attacker text', () => {
        // Even if a caller passed a hostile value, Number.isFinite gates it out and
        // Math.trunc coerces; there is no string path into the patch body.
        expect(buildSeqPatch({ messageIri: 'https://p/d#m', seq: '1; DROP' })).toBe('');
        expect(buildSeqPatch({ messageIri: 'https://p/d#m', seq: 42.9 })).toContain(`<${P.seq}> 42 .`);
    });
});

describe('edit/delete builders escape hostile input', () => {
    it('buildEditPatch keeps an injection attempt inside an escaped literal', () => {
        const patch = buildEditPatch({ messageIri: 'https://p/d#m', newContent: 'x" . <e> <p> "y' });
        expect(patch).toContain('x\\"');                                  // quote escaped
        expect(patch.match(new RegExp(P.content.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g')))
            .toHaveLength(3);                                             // exactly DELETE?/INSERT/WHERE
    });

    it('buildDeletePatch escapes the tombstone timestamp literal', () => {
        const patch = buildDeletePatch({ messageIri: 'https://p/d#m', deletedIso: '2026" . <e> <p> "z' });
        expect(patch).toContain('2026\\"');
        expect(patch.match(/INSERT DATA/g)).toHaveLength(1);
    });
});

describe('order comparator is robust to hostile/degenerate input', () => {
    it('never throws on null / missing fields and stays a total order', () => {
        expect(() => [null, {}, { seq: 1 }, { timestamp: 'z' }].sort(compareByOrder)).not.toThrow();
    });
});
