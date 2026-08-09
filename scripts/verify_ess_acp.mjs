#!/usr/bin/env node
/**
 * A2.3: verify Proxion's ACP authoring + Link-header discovery against a LIVE
 * Inrupt ESS pod (pod.inrupt.com), which uses Access Control Policy.
 *
 * You run this (not the assistant): per Inrupt's warning, your CLIENT_SECRET must
 * not be shared. Steps:
 *   1. Create a free pod at https://signup.pod.inrupt.com/
 *   2. Register a client at https://login.inrupt.com/registration.html
 *      -> gives a Client ID + Client Secret.
 *   3. Find your pod storage root (https://storage.inrupt.com/<id>/); the pod
 *      browser at https://podbrowser.inrupt.com shows it, or read pim:storage
 *      from your WebID https://id.inrupt.com/<username>.
 *   4. Install the auth lib and run:
 *        npm i @inrupt/solid-client-authn-node
 *        PROXION_ESS_CLIENT_ID=... PROXION_ESS_CLIENT_SECRET=... \
 *        PROXION_ESS_STORAGE=https://storage.inrupt.com/<id>/ \
 *        node scripts/verify_ess_acp.mjs
 *   5. Paste the output back; it never prints your secret.
 *
 * The ACP-authoring/discovery logic below is inlined from web/acl.js verbatim so
 * this file is self-contained. If ESS rejects the ACR, the error + our ACR body
 * are printed so the shape can be fixed, then acl.js updated to match.
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
function detectAclModel(linkHeader) {
    const links = parseLinkHeader(linkHeader);
    if (links.some((l) => l.rel === ACP_ACCESS_CONTROL_REL)) return 'acp';
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
    const model = detectAclModel(link);
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
