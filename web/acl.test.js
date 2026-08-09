// acl.test.js — R100 A2.1: header-based access-control discovery + model detect.
import { describe, it, expect, vi, beforeEach } from 'vitest';

import {
    parseLinkHeader, accessControlUrl, detectAclModel, ACP_ACCESS_CONTROL_REL,
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
});

// ── discoverAccessControl (network wrapper in pod.js) ───────────────────────
let _session = null;
vi.mock('./auth.js', () => ({
    get solidSession() { return _session; },
    podStorageRoot: () => 'https://alice.example/',
}));

import { discoverAccessControl } from './pod.js';

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
