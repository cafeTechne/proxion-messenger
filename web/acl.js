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
 * Which access-control model the server uses for a resource, from its Link header
 * (and the discovered ACR URL): 'acp' if it advertises the ACP accessControl
 * relation OR the access-control resource is a `.acr` (how CSS-ACP exposes it, via
 * rel="acl"); else 'wac' if it advertises rel="acl"; else null (caller falls back
 * to the .acl convention + WAC). Verified against a live CSS-ACP server: it
 * advertises `<...foo.acr>; rel="acl"`, so the rel alone cannot distinguish ACP
 * from WAC and the `.acr` suffix is the reliable signal.
 */
export function detectAclModel(linkHeader, acrUrl) {
    const links = parseLinkHeader(linkHeader);
    if (links.some((l) => l.rel === ACP_ACCESS_CONTROL_REL)) return 'acp';
    if (acrUrl && /\.acr(?:$|[?#])/.test(acrUrl)) return 'acp';
    if (links.some((l) => l.rel === 'acl')) return 'wac';
    return null;
}

// Only web IDs (absolute http(s) IRIs) may be named as agents in an ACR.
function _isWebId(w) {
    return typeof w === 'string' && /^https?:\/\/\S+$/.test(w) && !/[\s<>"]/.test(w);
}

/**
 * Build an ACP Access Control Resource (turtle) granting the owner full control
 * and each member the modes in `memberModes` (default acl:Read), over the
 * resource and its members (the ACP analogue of WAC acl:default). Access Control
 * Policy v0.9.0. For a shared chat container, pass `acl:Read, acl:Write,
 * acl:Append` so participants can POST (the ACP analogue of buildChatAcl).
 *
 * NOTE (R100 A2): authored from the spec and structurally unit-tested, but NOT
 * yet verified against a live Inrupt ESS. The exact ACR association (acp:resource
 * vs. link-only), memberAccessControl semantics, and content type may need
 * adjustment once tested on a real ACP server. Until then this only activates
 * when a server advertises the ACP model, so it cannot affect WAC servers (CSS).
 */
export function buildAcpAcr(ownerWebId, memberWebIds, resourceUrl, memberModes = 'acl:Read', publicModes = null) {
    if (!_isWebId(ownerWebId)) throw new Error('Invalid owner WebID');
    const members = (memberWebIds || []).filter(_isWebId);
    const memberAgents = members.map((w) => `<${w}>`).join(', ');
    const membersBlock = members.length > 0
        ? `\n<#members-ac> a acp:AccessControl; acp:apply <#members-policy>.\n` +
          `<#members-policy> a acp:Policy; acp:allow ${memberModes}; acp:anyOf <#members-matcher>.\n` +
          `<#members-matcher> a acp:Matcher; acp:agent ${memberAgents}.\n`
        : '';
    // Optional public grant (the ACP analogue of WAC's foaf:Agent authorization):
    // a matcher on acp:PublicAgent. Used for the public-Append drop-box inboxes.
    const publicBlock = publicModes
        ? `\n<#public-ac> a acp:AccessControl; acp:apply <#public-policy>.\n` +
          `<#public-policy> a acp:Policy; acp:allow ${publicModes}; acp:anyOf <#public-matcher>.\n` +
          `<#public-matcher> a acp:Matcher; acp:agent acp:PublicAgent.\n`
        : '';
    const controls = ['<#owner-ac>',
        ...(members.length > 0 ? ['<#members-ac>'] : []),
        ...(publicModes ? ['<#public-ac>'] : []),
    ].join(', ');
    return (
        `@prefix acp: <http://www.w3.org/ns/solid/acp#>.\n` +
        `@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n\n` +
        `<> a acp:AccessControlResource;\n` +
        `   acp:resource <${resourceUrl}>;\n` +
        `   acp:accessControl ${controls};\n` +
        `   acp:memberAccessControl ${controls}.\n\n` +
        `<#owner-ac> a acp:AccessControl; acp:apply <#owner-policy>.\n` +
        `<#owner-policy> a acp:Policy;\n` +
        `   acp:allow acl:Read, acl:Write, acl:Control;\n` +
        `   acp:anyOf <#owner-matcher>.\n` +
        `<#owner-matcher> a acp:Matcher; acp:agent <${ownerWebId}>.\n` +
        membersBlock +
        publicBlock
    );
}
