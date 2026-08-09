#!/usr/bin/env node
/**
 * A2.3: verify Proxion's ACP authoring + Link-header discovery against a LIVE
 * Access-Control-Policy server. Works against any ACP server via env vars
 * (oidcIssuer, client credentials, storage root).
 *
 * PRIMARY PATH (reproducible, no account needed): the Community Solid Server in
 * ACP mode. This is what A2.3 was verified against (Inrupt's free ESS/PodSpaces
 * was retired in 2026 in favor of enterprise wallets). Recipe:
 *   1. In a scratch dir:  npm i @solid/community-server @inrupt/solid-client-authn-node
 *   2. Start CSS in ACP mode:
 *        npx @solid/community-server -c @css:config/file-acp.json -p 3456 -f ./cssdata -b http://localhost:3456/
 *   3. Provision an account + client credentials (Python, uses our css_setup):
 *        from proxion_messenger_core.css_setup import CssAccountManager
 *        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
 *        c,pod,wid = CssAccountManager('http://localhost:3456').connect_agent(
 *            Ed25519PrivateKey.generate(),'a@example.org','pw12345678')
 *        # -> c.client_id, c.client_secret, pod
 *   4. Run:
 *        PROXION_ESS_CLIENT_ID=... PROXION_ESS_CLIENT_SECRET=... \
 *        PROXION_ESS_OIDC_ISSUER=http://localhost:3456/ \
 *        PROXION_ESS_STORAGE=http://localhost:3456/<account>/ \
 *        node scripts/verify_ess_acp.mjs
 *
 * INRUPT ESS (if you have enterprise access): set OIDC_ISSUER=https://login.inrupt.com,
 * register a client there, and point STORAGE at your storage root.
 *
 * The ACP-authoring/discovery logic below is inlined from web/acl.js verbatim so
 * this file is self-contained. If the server rejects the ACR, the error + our ACR
 * body are printed so the shape can be fixed, then acl.js updated to match.
 * Do not share your CLIENT_SECRET; the harness never prints it.
 */
import { Session } from '@inrupt/solid-client-authn-node';

// ── inlined from web/acl.js (keep in sync) ──────────────────────────────────
const ACP_ACCESS_CONTROL_REL = 'http://www.w3.org/ns/solid/acp#accessControl';
function parseLinkHeader(value) {
    const out = [];
    if (!value || typeof value !== 'string') return out;
    const linkRe = /<([^>]*)>\s*((?:;[^,<]*)*)/g;
    let m;
    while ((m = linkRe.exec(value)) !== null) {
        const uri = m[1], params = m[2] || '', rels = [];
        const relRe = /rel\s*=\s*(?:"([^"]*)"|([^;,\s]+))/g;
        let rm;
        while ((rm = relRe.exec(params)) !== null) {
            (rm[1] || rm[2] || '').trim().split(/\s+/).forEach((r) => { if (r) rels.push(r); });
        }
        if (rels.length === 0) out.push({ uri, rel: '' });
        else rels.forEach((rel) => out.push({ uri, rel }));
    }
    return out;
}
function resolveAgainst(uri, baseUrl) { try { return new URL(uri, baseUrl).href; } catch { return uri; } }
function accessControlUrl(linkHeader, resourceUrl) {
    const links = parseLinkHeader(linkHeader);
    const acp = links.find((l) => l.rel === ACP_ACCESS_CONTROL_REL);
    if (acp) return resolveAgainst(acp.uri, resourceUrl);
    const acl = links.find((l) => l.rel === 'acl');
    return acl ? resolveAgainst(acl.uri, resourceUrl) : null;
}
function detectAclModel(linkHeader, acrUrl) {
    const links = parseLinkHeader(linkHeader);
    if (links.some((l) => l.rel === ACP_ACCESS_CONTROL_REL)) return 'acp';
    if (acrUrl && /\.acr(?:$|[?#])/.test(acrUrl)) return 'acp';   // CSS-ACP: .acr via rel="acl"
    if (links.some((l) => l.rel === 'acl')) return 'wac';
    return null;
}
function _isWebId(w) { return typeof w === 'string' && /^https?:\/\/\S+$/.test(w) && !/[\s<>"]/.test(w); }
function buildAcpAcr(ownerWebId, memberWebIds, resourceUrl) {
    if (!_isWebId(ownerWebId)) throw new Error('Invalid owner WebID');
    const members = (memberWebIds || []).filter(_isWebId);
    const memberAgents = members.map((w) => `<${w}>`).join(', ');
    const membersBlock = members.length > 0
        ? `\n<#members-ac> a acp:AccessControl; acp:apply <#members-policy>.\n` +
          `<#members-policy> a acp:Policy; acp:allow acl:Read; acp:anyOf <#members-matcher>.\n` +
          `<#members-matcher> a acp:Matcher; acp:agent ${memberAgents}.\n`
        : '';
    const controls = members.length > 0 ? '<#owner-ac>, <#members-ac>' : '<#owner-ac>';
    return (
        `@prefix acp: <http://www.w3.org/ns/solid/acp#>.\n` +
        `@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n\n` +
        `<> a acp:AccessControlResource;\n   acp:resource <${resourceUrl}>;\n` +
        `   acp:accessControl ${controls};\n   acp:memberAccessControl ${controls}.\n\n` +
        `<#owner-ac> a acp:AccessControl; acp:apply <#owner-policy>.\n` +
        `<#owner-policy> a acp:Policy;\n   acp:allow acl:Read, acl:Write, acl:Control;\n   acp:anyOf <#owner-matcher>.\n` +
        `<#owner-matcher> a acp:Matcher; acp:agent <${ownerWebId}>.\n` + membersBlock
    );
}

// ── harness ─────────────────────────────────────────────────────────────────
const CID = process.env.PROXION_ESS_CLIENT_ID;
const SECRET = process.env.PROXION_ESS_CLIENT_SECRET;
const ISSUER = process.env.PROXION_ESS_OIDC_ISSUER || 'https://login.inrupt.com';
const STORAGE = process.env.PROXION_ESS_STORAGE;
const MEMBER = process.env.PROXION_ESS_MEMBER || 'https://id.inrupt.com/example-proxion-member';

if (!CID || !SECRET || !STORAGE) {
    console.error('Set PROXION_ESS_CLIENT_ID, PROXION_ESS_CLIENT_SECRET, PROXION_ESS_STORAGE. See the header.');
    process.exit(1);
}

function step(ok, label, extra = '') { console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra ? '  ' + extra : ''}`); }

const session = new Session();
await session.login({ clientId: CID, clientSecret: SECRET, oidcIssuer: ISSUER, tokenType: 'Bearer' });
if (!session.info.isLoggedIn) { step(false, 'login'); process.exit(2); }
const me = session.info.webId;
step(true, 'login', `as ${me}`);

const base = STORAGE.replace(/\/?$/, '/');
const container = `${base}proxion-acp-test-${Date.now()}/`;
let failed = false;
try {
    let r = await session.fetch(container, { method: 'PUT', headers: { 'Content-Type': 'text/turtle' }, body: '' });
    step(r.ok || r.status === 201, 'create test container', `HTTP ${r.status}`);

    const head = await session.fetch(container, { method: 'HEAD' });
    const link = head.headers.get('link');
    const acrUrl = accessControlUrl(link, container);
    const model = detectAclModel(link, acrUrl);
    step(model === 'acp', 'discover access-control model is ACP', `model=${model}`);
    step(!!acrUrl, 'discover ACR URL from Link header', acrUrl || '(none)');
    if (!acrUrl) throw new Error('no ACR URL advertised');

    const acr = buildAcpAcr(me, [MEMBER], container);
    r = await session.fetch(acrUrl, { method: 'PUT', headers: { 'Content-Type': 'text/turtle' }, body: acr });
    if (!r.ok) {
        step(false, 'ESS accepts our ACP ACR', `HTTP ${r.status}`);
        console.error('\n--- ESS response ---\n' + (await r.text()) + '\n--- our ACR ---\n' + acr);
        failed = true;
    } else {
        step(true, 'ESS accepts our ACP ACR', `HTTP ${r.status}`);
        r = await session.fetch(acrUrl, { headers: { Accept: 'text/turtle' } });
        const back = await r.text();
        step(r.ok && /acp:AccessControl/.test(back), 'ACR persisted + reads back', `HTTP ${r.status}`);
    }
} catch (e) {
    step(false, 'verification', e.message); failed = true;
} finally {
    await session.fetch(container, { method: 'DELETE' }).catch(() => {});
    await session.logout().catch(() => {});
}
console.log(failed ? '\nRESULT: ACP path needs a fix (see above).' : '\nRESULT: ACP path verified against live ESS.');
process.exit(failed ? 3 : 0);
