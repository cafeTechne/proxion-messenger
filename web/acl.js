// acl.js — access-control discovery + model detection (R100 A2).
//
// Proxion historically derived the ACL URL by appending ".acl" to a resource
// (works on CSS/NSS by convention, but the Solid Protocol says to discover it
// from the `Link: rel="acl"` header, never by string manipulation). It also only
// speaks Web Access Control (WAC); Inrupt ESS uses Access Control Policy (ACP).
// This module provides the pure discovery/detection helpers so pod.js can find
// the right access-control resource and pick the right authoring format.
//
// Pure functions only (no network); the network wrapper lives in pod.js so this
// stays trivially unit-testable.

// ACP resources advertise their Access Control Resource with this link relation;
// WAC advertises the ACL with rel="acl". Some ACP servers reuse rel="acl" to
// point at the ACR, so callers should treat the URL and the model separately.
export const ACP_ACCESS_CONTROL_REL = 'http://www.w3.org/ns/solid/acp#accessControl';

/**
 * Parse an HTTP `Link` header into `{ uri, rel }` entries (one per rel token,
 * since a link may carry a space-separated rel list). Relative URIs are returned
 * verbatim; resolve them against the resource with `resolveAgainst`.
 */
export function parseLinkHeader(value) {
    const out = [];
    if (!value || typeof value !== 'string') return out;
    const linkRe = /<([^>]*)>\s*((?:;[^,<]*)*)/g;
    let m;
    while ((m = linkRe.exec(value)) !== null) {
        const uri = m[1];
        const params = m[2] || '';
        const rels = [];
        const relRe = /rel\s*=\s*(?:"([^"]*)"|([^;,\s]+))/g;
        let rm;
        while ((rm = relRe.exec(params)) !== null) {
            const raw = (rm[1] || rm[2] || '').trim();
            raw.split(/\s+/).forEach((r) => { if (r) rels.push(r); });
        }
        if (rels.length === 0) out.push({ uri, rel: '' });
        else rels.forEach((rel) => out.push({ uri, rel }));
    }
    return out;
}

/** Resolve a (possibly relative) Link URI against the resource it came from. */
export function resolveAgainst(uri, baseUrl) {
    try { return new URL(uri, baseUrl).href; } catch (_) { return uri; }
}

/**
 * The access-control resource URL advertised for `resourceUrl`, resolved to an
 * absolute URL, or null if the header advertises none. Prefers the ACP
 * accessControl link, then rel="acl".
 */
export function accessControlUrl(linkHeader, resourceUrl) {
    const links = parseLinkHeader(linkHeader);
    const acp = links.find((l) => l.rel === ACP_ACCESS_CONTROL_REL);
    if (acp) return resolveAgainst(acp.uri, resourceUrl);
    const acl = links.find((l) => l.rel === 'acl');
    if (acl) return resolveAgainst(acl.uri, resourceUrl);
    return null;
}

/**
 * Which access-control model the server uses for a resource, from its Link
 * header: 'acp' if it advertises the ACP accessControl relation, else 'wac' if it
 * advertises rel="acl", else null (unknown; caller falls back to the .acl
 * convention + WAC).
 */
export function detectAclModel(linkHeader) {
    const links = parseLinkHeader(linkHeader);
    if (links.some((l) => l.rel === ACP_ACCESS_CONTROL_REL)) return 'acp';
    if (links.some((l) => l.rel === 'acl')) return 'wac';
    return null;
}
