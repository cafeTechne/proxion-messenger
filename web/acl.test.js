// acl.test.js — R100 A2.1: header-based access-control discovery + model detect.
import { describe, it, expect, vi, beforeEach } from 'vitest';

import {
    parseLinkHeader, accessControlUrl, detectAclModel, buildAcpAcr, ACP_ACCESS_CONTROL_REL,
} from './acl.js';

const RES = 'https://alice.example/proxion/rooms/r1/';

describe('parseLinkHeader', () => {
    it('parses a single rel=acl link', () => {
        expect(parseLinkHeader('<r1.acl>; rel="acl"')).toEqual([{ uri: 'r1.acl', rel: 'acl' }]);
    });
    it('parses multiple links and space-separated rels', () => {
        const h = '<a.acl>; rel="acl", <desc>; rel="describedby", <x>; rel="type foaf:Agent"';
        const parsed = parseLinkHeader(h);
        expect(parsed).toContainEqual({ uri: 'a.acl', rel: 'acl' });
        expect(parsed).toContainEqual({ uri: 'desc', rel: 'describedby' });
        expect(parsed).toContainEqual({ uri: 'x', rel: 'type' });
        expect(parsed).toContainEqual({ uri: 'x', rel: 'foaf:Agent' });
    });
    it('is safe on empty/null input', () => {
        expect(parseLinkHeader('')).toEqual([]);
        expect(parseLinkHeader(null)).toEqual([]);
    });
});

describe('accessControlUrl', () => {
    it('resolves a relative rel=acl URI against the resource', () => {
        expect(accessControlUrl('<r1.acl>; rel="acl"', RES))
            .toBe('https://alice.example/proxion/rooms/r1/r1.acl');
    });
    it('prefers the ACP accessControl link over rel=acl', () => {
        const h = `<r1.acl>; rel="acl", <acr>; rel="${ACP_ACCESS_CONTROL_REL}"`;
        expect(accessControlUrl(h, RES)).toBe('https://alice.example/proxion/rooms/r1/acr');
    });
    it('returns null when no access-control link is advertised', () => {
        expect(accessControlUrl('<desc>; rel="describedby"', RES)).toBeNull();
    });
});

describe('detectAclModel', () => {
    it('detects acp, wac, or unknown', () => {
        expect(detectAclModel(`<acr>; rel="${ACP_ACCESS_CONTROL_REL}"`)).toBe('acp');
        expect(detectAclModel('<r1.acl>; rel="acl"')).toBe('wac');
        expect(detectAclModel('<desc>; rel="describedby"')).toBeNull();
    });

    it('detects ACP from a .acr ACR advertised via rel="acl" (CSS-ACP, verified live)', () => {
        // CSS in ACP mode advertises <...foo.acr>; rel="acl", so the rel alone
        // says wac; the .acr suffix is the reliable ACP signal.
        expect(detectAclModel('<foo.acr>; rel="acl"', 'https://p/foo.acr')).toBe('acp');
        expect(detectAclModel('<foo.acl>; rel="acl"', 'https://p/foo.acl')).toBe('wac');
    });
});

describe('buildAcpAcr', () => {
    const OWNER = 'https://alice.example/profile/card#me';
    const M1 = 'https://bob.example/profile/card#me';

    it('grants the owner full control and binds the resource', () => {
        const acr = buildAcpAcr(OWNER, [], RES);
        expect(acr).toContain('a acp:AccessControlResource');
        expect(acr).toContain(`acp:resource <${RES}>`);
        expect(acr).toContain(`<#owner-matcher> a acp:Matcher; acp:agent <${OWNER}>`);
        expect(acr).toMatch(/acp:allow acl:Read, acl:Write, acl:Control/);
        expect(acr).toContain('acp:memberAccessControl');   // inheritance to members
    });

    it('grants members read when present, and omits the block otherwise', () => {
        const withM = buildAcpAcr(OWNER, [M1], RES);
        expect(withM).toContain('<#members-matcher> a acp:Matcher; acp:agent <' + M1 + '>');
        expect(withM).toMatch(/<#members-policy> a acp:Policy; acp:allow acl:Read/);
        const noM = buildAcpAcr(OWNER, [], RES);
        expect(noM).not.toContain('members-ac');
    });

    it('filters non-WebID members and rejects a bad owner', () => {
        const acr = buildAcpAcr(OWNER, ['not-a-webid', M1], RES);
        expect(acr).toContain(`<${M1}>`);
        expect(acr).not.toContain('not-a-webid');
        expect(() => buildAcpAcr('nope', [], RES)).toThrow();
    });

    it('grants members the requested modes (chat participants get read/write/append)', () => {
        const acr = buildAcpAcr(OWNER, [M1], RES, 'acl:Read, acl:Write, acl:Append');
        expect(acr).toMatch(/<#members-policy> a acp:Policy; acp:allow acl:Read, acl:Write, acl:Append/);
        // owner still full control
        expect(acr).toMatch(/acp:allow acl:Read, acl:Write, acl:Control/);
    });

    it('adds a public grant (inbox drop-boxes: public Append) via acp:PublicAgent', () => {
        const acr = buildAcpAcr(OWNER, [], RES, 'acl:Read', 'acl:Append');
        expect(acr).toContain('<#public-matcher> a acp:Matcher; acp:agent acp:PublicAgent');
        expect(acr).toMatch(/<#public-policy> a acp:Policy; acp:allow acl:Append/);
        expect(acr).toContain('<#public-ac>');           // wired into the controls
        expect(acr).not.toContain('members-ac');         // no members here
    });
});

// ── discoverAccessControl + grant routing (network wrapper in pod.js) ────────
let _session = null;
vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => 'https://alice.example/',
}));

import {
    discoverAccessControl, podSetContainerAcl,
    buildSparqlUpdate, buildN3Patch, patchFormatFor, podRdfPatch, podWriteReactionAction,
} from './pod.js';

describe('podWriteReactionAction (R101 reaction interop)', () => {
    function patchSession() {
        const patches = [];
        _session = {
            info: { isLoggedIn: true },
            fetch: vi.fn(async (url, opts) => {
                if (opts && opts.method === 'PATCH') { patches.push({ url, body: opts.body }); return { ok: true }; }
                return { headers: { get: () => null } };   // HEAD: no Accept-Patch -> SPARQL
            }),
        };
        return patches;
    }

    it('inserts a schema:LikeAction on add, into the message day file', async () => {
        const p = patchSession();
        const ok = await podWriteReactionAction('r1', 'm1', '2026-08-08T10:00:00Z',
            '👍', 'https://alice.example/profile/card#me', true);
        expect(ok).toBe(true);
        expect(p).toHaveLength(1);
        expect(p[0].url).toContain('/2026/08/08/');    // date-partitioned day file
        expect(p[0].body).toContain('INSERT DATA');
        expect(p[0].body).toContain('http://schema.org/LikeAction');
    });

    it('deletes the same triples on un-react', async () => {
        const p = patchSession();
        await podWriteReactionAction('r1', 'm1', '2026-08-08T10:00:00Z', '👍', null, false);
        expect(p[0].body).toContain('DELETE DATA');
    });

    it('is a no-op without the message timestamp', async () => {
        const p = patchSession();
        expect(await podWriteReactionAction('r1', 'm1', null, '👍', null, true)).toBe(false);
        expect(p).toHaveLength(0);
    });
});

describe('RDF patch builders (R101.3)', () => {
    const T = ['<a> <b> "c" .'];
    it('buildSparqlUpdate: INSERT/DELETE DATA for concrete triples', () => {
        expect(buildSparqlUpdate({ inserts: T })).toContain('INSERT DATA {');
        const both = buildSparqlUpdate({ inserts: T, deletes: ['<a> <b> "old" .'] });
        expect(both).toMatch(/DELETE DATA[\s\S]*"old"[\s\S]*INSERT DATA[\s\S]*"c"/);
    });
    it('buildSparqlUpdate: DELETE/INSERT ... WHERE when where is given', () => {
        const b = buildSparqlUpdate({ inserts: T, deletes: ['<a> <b> ?o .'], where: ['<a> <b> ?o .'] });
        expect(b).toContain('WHERE {');
        expect(b).not.toContain('DELETE DATA');
    });
    it('buildN3Patch: solid:InsertDeletePatch with the right clauses', () => {
        const b = buildN3Patch({ inserts: T, deletes: ['<a> <b> "old" .'], where: ['<a> <b> ?o .'] });
        expect(b).toContain('a solid:InsertDeletePatch');
        expect(b).toContain('solid:inserts {');
        expect(b).toContain('solid:deletes {');
        expect(b).toContain('solid:where {');
        expect(b).toContain('@prefix solid:');
    });
    it('patchFormatFor prefers n3 only when advertised', () => {
        expect(patchFormatFor('text/n3, application/sparql-update')).toBe('n3');
        expect(patchFormatFor('application/sparql-update')).toBe('sparql');
        expect(patchFormatFor(null)).toBe('sparql');
    });
});

describe('podRdfPatch negotiation', () => {
    function patchSession(acceptPatch) {
        const calls = [];
        _session = {
            info: { isLoggedIn: true },
            fetch: vi.fn(async (url, opts) => {
                if (opts && opts.method === 'PATCH') { calls.push(opts); return { ok: true }; }
                return { headers: { get: (k) => (k.toLowerCase() === 'accept-patch' ? acceptPatch : null) } };
            }),
        };
        return calls;
    }
    it('sends N3 Patch when the server advertises text/n3', async () => {
        const calls = patchSession('text/n3');
        await podRdfPatch('https://x/card', { inserts: ['<a> <b> "c" .'] });
        expect(calls[0].headers['Content-Type']).toBe('text/n3');
        expect(calls[0].body).toContain('solid:InsertDeletePatch');
    });
    it('falls back to SPARQL Update otherwise', async () => {
        const calls = patchSession('application/sparql-update');
        await podRdfPatch('https://x/card', { inserts: ['<a> <b> "c" .'] });
        expect(calls[0].headers['Content-Type']).toBe('application/sparql-update');
        expect(calls[0].body).toContain('INSERT DATA');
    });
});

describe('discoverAccessControl', () => {
    beforeEach(() => { _session = null; });

    it('uses the rel=acl URL from the Link header', async () => {
        _session = { info: { isLoggedIn: true }, fetch: vi.fn(async () => ({
            headers: { get: (k) => (k.toLowerCase() === 'link' ? '<r1.acl>; rel="acl"' : null) },
        })) };
        const { url, model } = await discoverAccessControl(RES);
        expect(url).toBe('https://alice.example/proxion/rooms/r1/r1.acl');
        expect(model).toBe('wac');
    });

    it('reports the acp model when advertised', async () => {
        _session = { info: { isLoggedIn: true }, fetch: vi.fn(async () => ({
            headers: { get: () => `<acr>; rel="${ACP_ACCESS_CONTROL_REL}"` },
        })) };
        const { model } = await discoverAccessControl(RES);
        expect(model).toBe('acp');
    });

    it('falls back to the .acl convention when nothing is advertised', async () => {
        _session = { info: { isLoggedIn: true }, fetch: vi.fn(async () => ({
            headers: { get: () => null },
        })) };
        const { url, model } = await discoverAccessControl(RES);
        expect(url).toBe(RES + '.acl');
        expect(model).toBe('wac');
    });

    it('falls back when the HEAD throws', async () => {
        _session = { info: { isLoggedIn: true }, fetch: vi.fn(async () => { throw new Error('down'); }) };
        const { url } = await discoverAccessControl(RES);
        expect(url).toBe(RES + '.acl');
    });
});

describe('podSetContainerAcl routing by model', () => {
    const OWNER = 'https://alice.example/profile/card#me';

    function sessionFor(linkHeader) {
        const puts = [];
        _session = {
            info: { isLoggedIn: true, webId: OWNER },
            fetch: vi.fn(async (url, opts) => {
                if (opts && opts.method === 'PUT') { puts.push({ url, body: opts.body }); return { ok: true }; }
                return { headers: { get: () => linkHeader } };   // HEAD
            }),
        };
        return puts;
    }

    it('writes an ACP ACR when the server advertises ACP', async () => {
        const puts = sessionFor(`<acr>; rel="${ACP_ACCESS_CONTROL_REL}"`);
        await podSetContainerAcl('proxion/rooms/r1/', OWNER, []);
        expect(puts).toHaveLength(1);
        expect(puts[0].url).toBe('https://alice.example/proxion/rooms/r1/acr');
        expect(puts[0].body).toContain('acp:AccessControlResource');
    });

    it('writes WAC turtle when the server advertises rel=acl', async () => {
        const puts = sessionFor('<r1.acl>; rel="acl"');
        await podSetContainerAcl('proxion/rooms/r1/', OWNER, []);
        expect(puts).toHaveLength(1);
        expect(puts[0].body).toContain('acl:Authorization');
        expect(puts[0].body).not.toContain('acp:AccessControlResource');
    });
});
