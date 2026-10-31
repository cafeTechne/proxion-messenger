// podio.js: the pod drop-box, identity and presence I/O, over an injected fetch.
//
// The browser build talks to its pod with the Solid session (pod.js wraps one
// instance of this over solidSession). The desktop app has no browser session:
// its gateway holds the pod credentials, so gwpod.js builds another instance
// whose fetch goes through the gateway's pod bridge. Both run the same code, so
// the drop-box protocol (webdm.js, webcalls.js, webjoin.js, webpresence.js) is
// identical in both builds.
//
//   createPodIo({ fetch, isLoggedIn, storageRoot, webId })
//     fetch(url, opts)  -> Response-like ({ ok, status, headers.get, text, json })
//     isLoggedIn()      -> whether the fetch is authenticated to our own pod
//     storageRoot()     -> our pod storage root (ends with '/'), or null
//     webId()           -> our WebID, or null

import { isPeerPodRootAllowed } from './ssrf.js';
import { parseInboxListing } from './ldn.js';
import { accessControlUrl, detectAclModel, buildAcpAcr } from './acl.js';

// A single small doc (one drop, one signer) gets the 64K cap; a container
// listing, which can legitimately hold many entries, gets a larger one.
export const MAX_POD_JSON_BYTES = 65536;
export const MAX_POD_LISTING_BYTES = 512 * 1024;

export const DM_INBOX_PATH = 'proxion/dm-inbox/';
export const CALL_INBOX_PATH = 'proxion/call-inbox/';
export const JOIN_INBOX_PATH = 'proxion/join-inbox/';
export const PRESENCE_PATH = 'proxion/presence.json';
export const SIGNER_PATH = 'proxion/identity/signer.json';
export const X25519_PUB_PATH = 'proxion/identity/x25519-pub.json';
const JOINED_ROOMS_PATH = 'proxion/joined-rooms.jsonld';
const MAX_DROPS = 200;
const MAX_JOINED_ROOMS = 200;

const _dropBoxUrl = (podRoot, path) => (podRoot ? podRoot.replace(/\/?$/, '/') + path : null);

/** The recipient's DM drop-box URL, from their pod storage root. */
export function dmInboxUrlFor(podRoot) { return _dropBoxUrl(podRoot, DM_INBOX_PATH); }
/** The callee's call-signaling drop-box URL, from their pod storage root. */
export function callInboxUrlFor(podRoot) { return _dropBoxUrl(podRoot, CALL_INBOX_PATH); }
/** The recipient's join-inbox URL, from their pod storage root. */
export function joinInboxUrlFor(podRoot) { return _dropBoxUrl(podRoot, JOIN_INBOX_PATH); }
/** The presence resource URL for a pod storage root. */
export function presenceUrlFor(podRoot) {
    if (!podRoot) return null;
    return podRoot.replace(/\/?$/, '/') + PRESENCE_PATH;
}

// The inbox ACL: owner full control, public Append (LDN norm), plus any extra
// read-only agents (R78: the gateway's WebID, so it can poll the inbox for us).
export function buildInboxAcl(inboxUrl, ownerWebId, readerWebIds = []) {
    let ttl = `@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n@prefix foaf: <http://xmlns.com/foaf/0.1/>.\n`
        + `<#owner> a acl:Authorization; acl:agent <${ownerWebId}>; acl:accessTo <${inboxUrl}>; acl:default <${inboxUrl}>; acl:mode acl:Read, acl:Write, acl:Control.\n`
        + `<#public> a acl:Authorization; acl:agentClass foaf:Agent; acl:accessTo <${inboxUrl}>; acl:mode acl:Append.\n`;
    const readers = [...new Set(readerWebIds.filter(w => w && w !== ownerWebId))];
    if (readers.length) {
        const agents = readers.map(w => `acl:agent <${w}>`).join('; ');
        ttl += `<#gatewayread> a acl:Authorization; ${agents}; acl:accessTo <${inboxUrl}>; acl:default <${inboxUrl}>; acl:mode acl:Read.\n`;
    }
    return ttl;
}

// The inbox/drop-box ACL body for the discovered access-control model: WAC turtle,
// or on an ACP server (ESS) an ACR with the same shape (owner full control, public
// Append, optional reader agents Read). ACP path is spec-authored, not yet
// live-verified; it only activates when the server advertises ACP, so WAC servers
// (CSS) are unaffected.
export function inboxAclBody(model, inboxUrl, ownerWebId, readerWebIds = []) {
    return model === 'acp'
        ? buildAcpAcr(ownerWebId, readerWebIds, inboxUrl, 'acl:Read', 'acl:Append')
        : buildInboxAcl(inboxUrl, ownerWebId, readerWebIds);
}

// Owner full control + public Read for a discoverable resource (the public type
// index), as WAC turtle or the ACP analogue (public via acp:PublicAgent). ACP path
// is spec-authored, not yet live-verified; it activates only when the server
// advertises ACP, so WAC servers (CSS) are unaffected.
export function publicReadAclBody(model, resourceUrl, ownerWebId) {
    if (model === 'acp') return buildAcpAcr(ownerWebId, [], resourceUrl, 'acl:Read', 'acl:Read');
    return `@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n@prefix foaf: <http://xmlns.com/foaf/0.1/>.\n`
        + `<#owner> a acl:Authorization; acl:agent <${ownerWebId}>; acl:accessTo <${resourceUrl}>; acl:mode acl:Read, acl:Write, acl:Control.\n`
        + `<#public> a acl:Authorization; acl:agentClass foaf:Agent; acl:accessTo <${resourceUrl}>; acl:mode acl:Read.\n`;
}

export async function sha256Hex(str) {
    const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
    return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
}

export function createPodIo({ fetch, isLoggedIn, storageRoot, webId }) {
    const _loggedIn = () => { try { return !!isLoggedIn(); } catch { return false; } };
    const _root = () => storageRoot() || null;
    const _peerRootAllowed = (peerRoot) => isPeerPodRootAllowed(peerRoot, _root());   // SSRF gate
    let _presenceAclWritten = false;

    // Fetch and JSON-parse an attacker-controlled pod document with a byte cap, so a
    // giant body cannot be buffered into memory and hang the tab. Checks
    // Content-Length first, then the actual text length, before JSON.parse.
    // Returns the parsed value, or null when the response is not ok or exceeds
    // maxBytes. Network errors propagate so callers keep their own logging/fallback.
    async function fetchJsonCapped(url, opts = {}, maxBytes = MAX_POD_JSON_BYTES) {
        const res = await fetch(url, opts);
        if (!res || !res.ok) return null;
        const len = Number(res.headers?.get?.('content-length'));
        if (Number.isFinite(len) && len > maxBytes) return null;
        if (typeof res.text === 'function') {
            const text = await res.text();
            if (text.length > maxBytes) return null;
            return JSON.parse(text);
        }
        return await res.json();
    }

    // The access-control resource for a URL and the server's model ('wac' / 'acp'),
    // from the Link header, falling back to the WAC '.acl' convention.
    async function discoverAccessControl(resourceUrl) {
        try {
            const res = await fetch(resourceUrl, { method: 'HEAD' });
            const link = (res && res.headers && res.headers.get) ? res.headers.get('link') : null;
            const url = accessControlUrl(link, resourceUrl);
            if (url) return { url, model: detectAclModel(link, url) || 'wac' };
        } catch (_) { /* fall through to the convention */ }
        return { url: resourceUrl + '.acl', model: 'wac' };
    }

    // PUT an inbox/drop-box ACL, retrying once on a non-ok response. Without checking
    // the response, a 403/500 was treated as success and the box stayed owner-only, so
    // peers' drops 403'd invisibly. Best effort: logs clearly and returns whether the
    // public-Append grant is in place; the caller still returns the inbox so the owner
    // is not blocked on their own resource by a transient ACL hiccup.
    async function putInboxAcl(aclUrl, body, label) {
        for (let attempt = 0; attempt < 2; attempt++) {
            try {
                const res = await fetch(aclUrl, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'text/turtle' },
                    body,
                });
                if (res && res.ok) return true;
                console.warn(`[pod] ${label} ACL write returned ${res && res.status}${attempt === 0 ? ', retrying' : ''}`);
            } catch (err) {
                console.warn(`[pod] ${label} ACL write failed${attempt === 0 ? ', retrying' : ''}:`, err);
            }
        }
        console.warn(`[pod] ${label} may not be publicly appendable; peers' drops could be refused`);
        return false;
    }

    /** Ensure OUR drop-box at `path` exists and is public-Append (owner full control). */
    async function _ensureDropBox(path, label) {
        const root = _root();
        const me = webId();
        if (!root || !me || !_loggedIn()) return null;
        const inboxUrl = root.replace(/\/?$/, '/') + path;
        try {
            const put = await fetch(inboxUrl, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'text/turtle',
                    Link: '<http://www.w3.org/ns/ldp#BasicContainer>; rel="type"',
                },
                body: '',
            });
            if (!put || !(put.ok || put.status === 409 || put.status === 405)) return null;
            try {
                const { url: aclUrl, model } = await discoverAccessControl(inboxUrl);
                await putInboxAcl(aclUrl, inboxAclBody(model, inboxUrl, me, []), label);
            } catch (err) {
                console.warn(`[pod] ${label} ACL failed:`, err);
            }
            return inboxUrl;
        } catch (err) {
            console.warn(`[pod] ensure ${label} failed:`, err);
            return null;
        }
    }

    /** POST a JSON object into a peer's public-Append drop-box at `path`. */
    async function _dropTo(peerPodRoot, path, obj) {
        const inbox = _dropBoxUrl(peerPodRoot, path);
        if (!inbox || !obj || !_loggedIn()) return false;
        if (!_peerRootAllowed(peerPodRoot)) { console.warn('[pod] refusing drop to unsafe peer root:', peerPodRoot); return false; }
        try {
            const res = await fetch(inbox, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(obj),
                redirect: 'error',   // never follow a peer redirect off the vetted host
            });
            return !!(res && res.ok);
        } catch (err) {
            console.warn('[pod] drop failed:', err);
            return false;
        }
    }

    /** Read our own drop-box at `path`: returns [{ url, item }] for each object. */
    async function _readDropBox(path) {
        const root = _root();
        if (!root || !_loggedIn()) return [];
        const inbox = root.replace(/\/?$/, '/') + path;
        let urls = [];
        try {
            const json = await fetchJsonCapped(inbox, { headers: { Accept: 'application/ld+json' } }, MAX_POD_LISTING_BYTES);
            if (!json) return [];
            urls = parseInboxListing(json, inbox);
        } catch (err) {
            console.warn('[pod] drop-box listing failed:', err);
            return [];
        }
        if (urls.length > MAX_DROPS) urls = urls.slice(0, MAX_DROPS);
        const out = [];
        for (const url of urls) {
            try {
                // Each dropped item is attacker-authored (the box is public-Append), so bound
                // its body before parse the same way the listing above is bounded.
                const item = await fetchJsonCapped(url, { headers: { Accept: 'application/json' } });
                if (item != null) out.push({ url, item });
            } catch { /* skip one unreadable drop */ }
        }
        return out;
    }

    /** Delete a consumed drop by URL. */
    async function _deleteDrop(url) {
        if (!url || !_loggedIn()) return false;
        try {
            const res = await fetch(url, { method: 'DELETE' });
            return !!(res && (res.ok || res.status === 404));
        } catch (err) {
            console.warn('[pod] delete drop failed:', err);
            return false;
        }
    }

    /**
     * Give an identity trust anchor (signer.json / x25519-pub.json) a public-read ACL.
     * These files carry only public data (a did:key + account did; an X25519 public
     * key), and a peer authenticating as itself must be able to READ them: R107 DM
     * sender verification and gateway-free peer-key discovery both fetch them from the
     * OTHER user's pod. Without it they inherit proxion/'s owner-only default, so a
     * peer 403s and the feature silently degrades to a cross-pod no-op.
     *
     * Public READ only, scoped to the single file via acl:accessTo (never write/append/
     * control, and no acl:default on proxion/ that would expose other private children
     * such as read state, scheduled messages or room data). Mirrors the presence
     * heartbeat / public type index ACL. D4: reads the ACL back to confirm the grant
     * took effect rather than trusting the PUT status alone; returns whether public
     * read is in place.
     */
    async function podPublishIdentityAcl(resourceUrl) {
        const me = webId();
        if (!resourceUrl || !me || !_loggedIn()) return false;
        try {
            const { url: aclUrl, model } = await discoverAccessControl(resourceUrl);
            const put = await fetch(aclUrl, {
                method: 'PUT',
                headers: { 'Content-Type': 'text/turtle' },
                body: publicReadAclBody(model, resourceUrl, me),
            });
            if (!put || !put.ok) {
                console.warn('[pod] identity ACL write returned', put && put.status);
                return false;
            }
            const check = await fetch(aclUrl, { headers: { Accept: 'text/turtle' } });
            if (!check || !check.ok) return false;
            const ttl = typeof check.text === 'function' ? await check.text() : '';
            return /acl:Read/.test(ttl);
        } catch (err) {
            console.warn('[pod] podPublishIdentityAcl failed:', err);
            return false;
        }
    }

    // ── DM sender identity (R107) ──
    // Publish this device's signing identity (its did:key, and the account did it is
    // certified under, if any) next to the public x25519 key. A recipient fetches it
    // from the SENDER's pod to decide whether a DM's envelope signer is allowed to
    // speak for that WebID: an attacker cannot write here, so it is the trust anchor.
    async function podPublishSigner(signerDid, accountDid) {
        const root = _root();
        if (!root || !signerDid || !_loggedIn()) return false;
        const url = root.replace(/\/?$/, '/') + SIGNER_PATH;
        try {
            const res = await fetch(url, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ version: 1, signer: signerDid, account_did: accountDid || null }),
            });
            if (!res || !res.ok) return false;
            // The signer is a trust anchor a PEER must read from OUR pod, so give it a
            // public-read ACL; without it the file inherits proxion/'s owner-only default
            // and a peer 403s, so R107 sender verification never reaches sender_verified
            // across pods. Best-effort (the file is already saved), logged on failure.
            await podPublishIdentityAcl(url);
            return true;
        } catch (err) {
            console.warn('[pod] podPublishSigner failed:', err);
            return false;
        }
    }

    /** Fetch a peer's published signer identity, or null. Guarded against SSRF. */
    async function podFetchPeerSigner(peerPodRoot) {
        if (!peerPodRoot || !_peerRootAllowed(peerPodRoot) || !_loggedIn()) return null;
        try {
            const d = await fetchJsonCapped(
                peerPodRoot.replace(/\/?$/, '/') + SIGNER_PATH,
                { headers: { Accept: 'application/json' }, redirect: 'error' },
            );
            if (d && typeof d.signer === 'string') {
                return { signer: d.signer, account_did: typeof d.account_did === 'string' ? d.account_did : null };
            }
        } catch { /* peer has not published a signer identity */ }
        return null;
    }

    // --- Pending join requests (W1) ---
    // The join inbox is public-Append, so anyone who knows a WebID can drop a
    // `join_approved`. To stop a forged approval from forcing a room in, the joiner
    // records a marker in their OWN pod (trustworthy, and cross-device) whenever they
    // actually request to join; an approval is only honored when a matching marker
    // exists. Keyed by a hash of (roomId, ownerWebId) so the path is a safe hex id no
    // matter what those strings contain.
    async function _pendingJoinPath(roomId, ownerWebId) {
        const id = await sha256Hex(String(roomId) + '\n' + String(ownerWebId));
        return `proxion/join-requests/${id}.jsonld`;
    }

    async function podWritePendingJoin(roomId, ownerWebId) {
        if (!roomId || !ownerWebId) return;
        const root = _root();
        if (!root || !_loggedIn()) return;
        const path = await _pendingJoinPath(roomId, ownerWebId);
        try {
            await fetch(root + path, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/ld+json' },
                body: JSON.stringify({
                    '@context': { px: 'https://proxion.dev/vocab/v1#' },
                    '@type': 'px:PendingJoin',
                    'px:roomId': String(roomId),
                    'px:ownerWebId': String(ownerWebId),
                    'px:updatedAt': new Date().toISOString(),
                }),
            });
        } catch (err) {
            console.warn('[pod] _writePxDoc failed:', path, err);
        }
    }

    async function podHasPendingJoin(roomId, ownerWebId) {
        const root = _root();
        if (!root || !_loggedIn() || !roomId || !ownerWebId) return false;
        try {
            const res = await fetch(root + await _pendingJoinPath(roomId, ownerWebId),
                { headers: { Accept: 'application/ld+json' } });
            if (!res.ok) return false;
            const doc = await res.json();
            // Match the fields too, so an astronomically unlikely hash collision can't
            // vouch for a different room/owner than the one requested.
            return doc?.['px:roomId'] === String(roomId) && doc?.['px:ownerWebId'] === String(ownerWebId);
        } catch { return false; }
    }

    async function podDeletePendingJoin(roomId, ownerWebId) {
        const root = _root();
        if (!root || !_loggedIn() || !roomId || !ownerWebId) return;
        try {
            await fetch(root + await _pendingJoinPath(roomId, ownerWebId), { method: 'DELETE' });
        } catch { /* best-effort cleanup */ }
    }

    // --- Joined rooms (web build) ---
    // A room we joined lives on its owner's pod, and nothing in our own pod pointed
    // at it, so it vanished on reload. This owner-only record lists the rooms we
    // joined ({ roomId, owner, ownerPodRoot, container, title }) so every device can
    // re-register them. The caller re-validates each entry before using it.

    /** The joined-room records, [] when there are none, or null if the read failed. */
    async function podReadJoinedRooms() {
        const root = _root();
        if (!root || !_loggedIn()) return null;
        try {
            const res = await fetch(root + JOINED_ROOMS_PATH, { headers: { Accept: 'application/ld+json' } });
            if (res && res.status === 404) return [];
            if (!res || !res.ok) return null;
            const text = await res.text();
            if (text.length > MAX_POD_LISTING_BYTES) return null;
            const doc = JSON.parse(text);
            const list = doc && doc['px:rooms'];
            return Array.isArray(list) ? list.slice(0, MAX_JOINED_ROOMS) : [];
        } catch (err) {
            console.warn('[pod] podReadJoinedRooms failed:', err);
            return null;
        }
    }

    /** Replace the joined-room records. Returns success. */
    async function podWriteJoinedRooms(list) {
        const root = _root();
        if (!root || !_loggedIn()) return false;
        try {
            const res = await fetch(root + JOINED_ROOMS_PATH, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/ld+json' },
                body: JSON.stringify({
                    '@context': { px: 'https://proxion.dev/vocab/v1#' },
                    '@type': 'px:JoinedRooms',
                    'px:rooms': (list || []).slice(0, MAX_JOINED_ROOMS),
                    'px:updatedAt': new Date().toISOString(),
                }),
            });
            return !!(res && res.ok);
        } catch (err) {
            console.warn('[pod] podWriteJoinedRooms failed:', err);
            return false;
        }
    }

    // ── Presence (R104): a public-read heartbeat peers poll or subscribe to ──
    //
    // Without a gateway to fan out presence, each user publishes a small public-read
    // proxion/presence.json ({ status, heartbeat }) refreshed on a timer. Contacts
    // derive online/away/offline from how fresh the heartbeat is (see webpresence.js).

    /** Publish our presence heartbeat. Writes a public-read ACL once. */
    async function podWritePresence(status) {
        const root = _root();
        const me = webId();
        if (!root || !me || !_loggedIn()) return false;
        const url = root.replace(/\/?$/, '/') + PRESENCE_PATH;
        try {
            const res = await fetch(url, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ v: 1, status, heartbeat: Date.now() }),
            });
            if (!res || !res.ok) return false;
            if (!_presenceAclWritten) {
                try {
                    const { url: aclUrl } = await discoverAccessControl(url);
                    const aclRes = await fetch(aclUrl, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'text/turtle' },
                        body: `@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n@prefix foaf: <http://xmlns.com/foaf/0.1/>.\n`
                            + `<#owner> a acl:Authorization; acl:agent <${me}>; acl:accessTo <${url}>; acl:mode acl:Read, acl:Write, acl:Control.\n`
                            + `<#public> a acl:Authorization; acl:agentClass foaf:Agent; acl:accessTo <${url}>; acl:mode acl:Read.\n`,
                    });
                    // Only latch on success, else a failed ACL PUT is never retried and
                    // peers see the user permanently offline (mirrors putInboxAcl).
                    if (aclRes && aclRes.ok) _presenceAclWritten = true;
                } catch (err) {
                    console.warn('[pod] presence ACL failed:', err);
                }
            }
            return true;
        } catch (err) {
            console.warn('[pod] podWritePresence failed:', err);
            return false;
        }
    }

    /** Read a peer's presence: { status, heartbeat } or null. */
    async function podReadPresence(peerPodRoot) {
        if (!_peerRootAllowed(peerPodRoot)) return null;
        const url = presenceUrlFor(peerPodRoot);
        if (!url || !_loggedIn()) return null;
        try {
            const res = await fetch(url, { headers: { Accept: 'application/json' }, redirect: 'error' });
            if (!res || !res.ok) return null;
            const d = await res.json();
            if (d && typeof d.status === 'string') {
                // Capture the server's Last-Modified (a CORS-safelisted header, readable
                // cross-origin) so freshness can be judged against one trusted clock
                // rather than the writer's self-reported, possibly-skewed, heartbeat.
                const lm = res.headers && res.headers.get && res.headers.get('Last-Modified');
                const serverMs = lm ? Date.parse(lm) : NaN;
                return {
                    status: d.status,
                    heartbeat: Number(d.heartbeat) || 0,
                    serverMs: Number.isFinite(serverMs) ? serverMs : null,
                };
            }
        } catch { /* peer has no presence / offline */ }
        return null;
    }

    return {
        fetchJsonCapped, discoverAccessControl, putInboxAcl,
        podPublishIdentityAcl, podPublishSigner, podFetchPeerSigner,
        podEnsureDmInbox: () => _ensureDropBox(DM_INBOX_PATH, 'DM inbox'),
        podDropDm: (recipientPodRoot, envelope) => _dropTo(recipientPodRoot, DM_INBOX_PATH, envelope),
        /** Read our DM inbox: returns [{ url, envelope }]. */
        podReadDmDrops: async () => (await _readDropBox(DM_INBOX_PATH)).map(({ url, item }) => ({ url, envelope: item })),
        podDeleteDmDrop: (url) => _deleteDrop(url),
        podEnsureCallInbox: () => _ensureDropBox(CALL_INBOX_PATH, 'call inbox'),
        /** Drop a WebRTC signaling message (offer/answer/ICE/hangup) into a peer's call inbox. */
        podDropSignal: (peerPodRoot, signal) => _dropTo(peerPodRoot, CALL_INBOX_PATH, signal),
        podReadSignals: async () => (await _readDropBox(CALL_INBOX_PATH)).map(({ url, item }) => ({ url, signal: item })),
        podDeleteSignal: (url) => _deleteDrop(url),
        // Both join requests (to a room owner) and approvals (back to a joiner) travel
        // through this one public-Append box, distinguished by a `kind` field.
        podEnsureJoinInbox: () => _ensureDropBox(JOIN_INBOX_PATH, 'join inbox'),
        podDropJoin: (recipientPodRoot, obj) => _dropTo(recipientPodRoot, JOIN_INBOX_PATH, obj),
        podReadJoins: async () => (await _readDropBox(JOIN_INBOX_PATH)).map(({ url, item }) => ({ url, msg: item })),
        podDeleteJoin: (url) => _deleteDrop(url),
        podWritePendingJoin, podHasPendingJoin, podDeletePendingJoin,
        podReadJoinedRooms, podWriteJoinedRooms,
        podWritePresence, podReadPresence,
        presenceUrlFor, dmInboxUrlFor, callInboxUrlFor, joinInboxUrlFor,
    };
}
