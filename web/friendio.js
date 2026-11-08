// Private friend index and actor-owned, recipient-readable relationship intents.
// Inbox snapshots are hints. Friendship is derived only from fresh resources at
// the actors' own pod URLs, never from an appendable notification's claims.
import { isDmPeerWebId, peerPodRootFromWebId } from './webdm.js';
import { isPeerPodRootAllowed } from './ssrf.js';
import { sha256Hex } from './podio.js';
import { accessControlUrl, detectAclModel, buildAcpAcr } from './acl.js';
import { isStrongEtag } from './room-membership.js';
import { signFriendIntent, verifyFriendIntent } from './dmsig.js';

export const MAX_FRIEND_PEERS = 200;
const MAX_BYTES = 256 * 1024;
const ID = /^[\w-]{1,128}$/;
export function friendWebId(value) {
    if (typeof value !== 'string' || value.length > 2048 || /[\s<>"{}|^`\\]/.test(value) || !isDmPeerWebId(value)) return null;
    try { return new URL(value).href; } catch { return null; }
}
function slot(value, states) {
    return value === null || (value && typeof value === 'object' && typeof value.id === 'string' && ID.test(value.id) && states.includes(value.state));
}
export function validFriendIntent(doc, actor, peer) {
    return doc && doc.v === 1 && doc.actor === actor && doc.peer === peer && actor !== peer
        && typeof doc.revision === 'string' && ID.test(doc.revision) && typeof doc.updated === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(doc.updated) && Number.isFinite(Date.parse(doc.updated))
        && new Date(doc.updated).toISOString() === doc.updated
        && slot(doc.offer, ['pending', 'withdrawn', 'removed']) && slot(doc.decision, ['accepted', 'declined', 'removed'])
        && typeof doc.proof === 'string' && doc.proof.length <= 512;
}
export function friendshipState(mine, theirs) {
    const myOffer = mine?.offer, theirOffer = theirs?.offer;
    const outgoing = myOffer?.state === 'pending';
    const incoming = theirOffer?.state === 'pending';
    const theirDecision = outgoing && theirs?.decision?.id === myOffer.id ? theirs.decision.state : null;
    const myDecision = incoming && mine?.decision?.id === theirOffer.id ? mine.decision.state : null;
    if ((outgoing && theirDecision === 'accepted') || (incoming && myDecision === 'accepted')) return 'friends';
    if (incoming && !myDecision) return 'incoming';
    if (outgoing && !theirDecision) return 'outgoing';
    return 'none';
}
export async function friendIntentUrl(root, peer) { return root + 'proxion/friends/peers/' + await sha256Hex(peer) + '.json'; }

export function createFriendIo({ fetch, getSession, getSigner }) {
    const session = () => {
        const value = getSession();
        const actor = friendWebId(value?.webId);
        if (!actor || !value?.ready || value.root !== peerPodRootFromWebId(actor)) return null;
        return { ...value, webId: actor };
    };
    const current = captured => !!captured && session()?.id === captured.id && session()?.webId === captured.webId && session()?.root === captured.root;
    const safePeer = (peer, captured) => !!captured && !!friendWebId(peer) && peer !== captured.webId
        && isPeerPodRootAllowed(peerPodRootFromWebId(peer), captured.root);
    const fail = (reason = 'unavailable') => ({ ok: false, reason });
    let preparation = null, preparedFor = null;
    async function json(url, captured, maxBytes = MAX_BYTES) {
        if (!current(captured)) return fail('account_changed');
        try {
            const response = await fetch(url, { cache: 'no-store', redirect: 'error', headers: { Accept: 'application/json' } });
            if (!current(captured)) return fail('account_changed');
            if (response.status === 404) return { ok: true, exists: false, doc: null, etag: null };
            if (!response.ok || Number(response.headers?.get('content-length')) > maxBytes) return fail();
            const text = await response.text();
            if (new TextEncoder().encode(text).length > maxBytes) return fail('invalid');
            return { ok: true, exists: true, doc: JSON.parse(text), etag: response.headers?.get('etag') };
        } catch { return fail(); }
    }
    async function acl(url, peer, captured, container = false) {
        if (!current(captured)) return false;
        let aclUrl = url + '.acl', model = 'wac';
        try {
            const response = await fetch(url, { method: 'HEAD', redirect: 'error' });
            const link = response.headers?.get('link');
            aclUrl = accessControlUrl(link, url) || aclUrl;
            model = detectAclModel(link, aclUrl) || 'wac';
        } catch { /* WAC convention */ }
        if (!current(captured) || new URL(aclUrl).origin !== new URL(captured.root).origin) return false;
        const body = model === 'acp' ? buildAcpAcr(captured.webId, peer ? [peer] : [], url)
            : `@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n<#owner> a acl:Authorization; acl:agent <${captured.webId}>; acl:accessTo <${url}>; ${container ? `acl:default <${url}>; ` : ''}acl:mode acl:Read, acl:Write, acl:Control.\n`
                + (peer ? `<#peer> a acl:Authorization; acl:agent <${peer}>; acl:accessTo <${url}>; acl:mode acl:Read.\n` : '');
        try {
            const result = await fetch(aclUrl, { method: 'PUT', redirect: 'error', headers: { 'Content-Type': 'text/turtle' }, body });
            return current(captured) && result.ok;
        } catch { return false; }
    }
    async function prepare(captured = session()) {
        if (!current(captured)) return false;
        if (preparedFor === captured.id) return true;
        if (preparation?.id === captured.id) return preparation.work;
        const work = (async () => {
            for (const path of ['proxion/', 'proxion/friends/', 'proxion/friends/peers/']) {
                if (!current(captured)) return false;
                const url = captured.root + path;
                let response;
                try { response = await fetch(url, { method: 'PUT', redirect: 'error',
                    headers: { 'Content-Type': 'text/turtle', Link: '<http://www.w3.org/ns/ldp#BasicContainer>; rel="type"' }, body: '' }); } catch { return false; }
                if (!(response.ok || [405, 409].includes(response.status))) return false;
                if (path !== 'proxion/' && !(await acl(url, null, captured, true))) return false;
            }
            if (current(captured)) preparedFor = captured.id;
            return current(captured);
        })();
        preparation = { id: captured.id, work };
        try { return await work; } finally { if (preparation?.work === work) preparation = null; }
    }
    async function readIndex(captured = session()) {
        const result = await json(captured?.root + 'proxion/friends/index.json', captured);
        if (!result.ok || !result.exists) return result.ok ? { ...result, entries: [] } : result;
        const doc = result.doc;
        if (doc?.v !== 1 || doc.owner !== captured.webId || !Array.isArray(doc.entries) || doc.entries.length > MAX_FRIEND_PEERS) return fail('invalid');
        const seen = new Set();
        for (const entry of doc.entries) {
            if (!safePeer(entry?.peer, captured) || seen.has(entry.peer)
                || (entry.delivered && (!ID.test(entry.delivered.revision || '') || !Number.isFinite(Date.parse(entry.delivered.updated))))) return fail('invalid');
            seen.add(entry.peer);
        }
        return { ...result, entries: doc.entries };
    }
    async function put(url, doc, version, captured) {
        if (!current(captured)) return fail('account_changed');
        if (version.exists && !isStrongEtag(version.etag)) return fail('validator_missing');
        const body = JSON.stringify(doc);
        if (new TextEncoder().encode(body).length > MAX_BYTES) return fail('limit');
        try {
            const response = await fetch(url, { method: 'PUT', redirect: 'error', headers: { 'Content-Type': 'application/json',
                ...(version.exists ? { 'If-Match': version.etag } : { 'If-None-Match': '*' }) }, body });
            if (!current(captured)) return fail('account_changed');
            return response.status === 412 ? fail('conflict') : response.ok ? { ok: true } : fail();
        } catch { return fail('confirmation_failed'); }
    }
    async function index(change, captured = session()) {
        if (!(await prepare(captured))) return fail();
        for (let attempt = 0; attempt < 6; attempt++) {
            const before = await readIndex(captured);
            if (!before.ok) return before;
            const entries = change(before.entries.map(entry => ({ ...entry })));
            if (!entries || entries.length > MAX_FRIEND_PEERS) return fail('limit');
            const saved = await put(captured.root + 'proxion/friends/index.json', { v: 1, owner: captured.webId, entries }, before, captured);
            if (saved.reason === 'conflict') continue;
            return saved;
        }
        return fail('conflict');
    }
    async function remember(peer, delivered = null, captured = session()) {
        if (!safePeer(peer, captured)) return fail('invalid');
        return index(entries => {
            let entry = entries.find(row => row.peer === peer);
            if (!entry) { entry = { peer }; entries.push(entry); }
            if (delivered && (!entry.delivered || entry.delivered.updated <= delivered.updated)) entry.delivered = { revision: delivered.revision, updated: delivered.updated };
            return entries;
        }, captured);
    }
    async function read(peer, own = false, captured = session()) {
        if (!safePeer(peer, captured)) return fail('invalid');
        const actor = own ? captured.webId : peer, target = own ? peer : captured.webId;
        const root = own ? captured.root : peerPodRootFromWebId(peer);
        const result = await json(await friendIntentUrl(root, target), captured, 16384);
        if (!result.ok || !result.exists) return result;
        // This exact actor-owned resource is the trust anchor, like signer.json.
        // Its protected URL survives device-key rotation; inbox copies are not anchors.
        if (!validFriendIntent(result.doc, actor, target) || !(await verifyFriendIntent(result.doc))) return fail('invalid');
        return result;
    }
    async function update(peer, change, captured = session()) {
        if (!safePeer(peer, captured)) return fail('invalid');
        const remembered = await remember(peer, null, captured);
        if (!remembered.ok) return remembered;
        for (let attempt = 0; attempt < 6; attempt++) {
            const before = await read(peer, true, captured);
            if (!before.ok) return before;
            const changed = await change(before.doc);
            if (!current(captured)) return fail('account_changed');
            if (changed?.reason) return fail(changed.reason);
            if (changed?.unchanged) return { ok: true, doc: before.doc };
            const signer = getSigner();
            if (!signer?.privKey || !signer?.signerDid) return fail('signer_missing');
            const doc = { v: 1, actor: captured.webId, peer, revision: crypto.randomUUID(),
                updated: new Date(Math.max(Date.now(), (Date.parse(before.doc?.updated) || 0) + 1)).toISOString(),
                offer: changed.offer ?? null, decision: changed.decision ?? null };
            doc.proof = await signFriendIntent(doc, signer.privKey, signer.signerDid);
            if (!doc.proof || !validFriendIntent(doc, captured.webId, peer)) return fail('signer_missing');
            const saved = await put(await friendIntentUrl(captured.root, peer), doc, before, captured);
            if (saved.reason === 'conflict') continue;
            if (!saved.ok) return saved;
            const confirmed = await read(peer, true, captured);
            if (confirmed.ok && confirmed.doc?.revision === doc.revision) return { ok: true, doc };
            if (!confirmed.ok) return fail('confirmation_failed');
        }
        return fail('conflict');
    }
    return { session, current, safePeer, prepare, readIndex, remember, read, update,
        grant: (peer, captured = session()) => safePeer(peer, captured) ? friendIntentUrl(captured.root, peer).then(url => acl(url, peer, captured)) : Promise.resolve(false) };
}
