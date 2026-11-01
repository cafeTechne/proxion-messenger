// podroomio.js: pod room I/O (Long Chat, room descriptor, members, type index)
// over an injected fetch.
//
// A pod room is a Solid Long Chat in its owner's pod that members append to.
// The browser build runs this over its Solid session (pod.js wraps one
// instance). The desktop app runs another instance over its gateway's pod
// bridge (crossrooms.js), so a room hosted in either build reads and writes the
// same way, with the same proofs.
//
//   createPodRoomIo({ fetch, isLoggedIn, storageRoot, webId, getSigner, fetchPeerSigner })
//     fetch(url, opts)        -> Response-like ({ ok, status, headers.get, text, json })
//     isLoggedIn()            -> whether the fetch is authenticated
//     storageRoot()           -> our pod storage root (ends with '/'), or null
//     webId()                 -> our WebID, or null
//     getSigner()             -> { privKey, signerDid } for Long Chat proofs, or null
//     fetchPeerSigner(root)   -> a maker's published signer (podio podFetchPeerSigner)

import { isPeerPodRootAllowed } from './ssrf.js';
import {
    chatRootUrl, indexUrlAt, channelIriAt, dayFileAt, messageIriAt,
    buildIndexTurtle, appendOps, buildChatAcl, roomIdFromChatContainer,
    parseLongChatJsonLd, verifyLongChatMessages,
} from './longchat.js';
import { signLongChat } from './dmsig.js';
import { peerPodRootFromWebId } from './webdm.js';
import {
    buildEmptyTypeIndex, buildRegisterPatch, buildDeregisterPatch,
    parsePublicTypeIndex, parseRegisteredContainers,
} from './typeindex.js';
import { parseRoomDescriptor } from './roomdesc.js';
import { accessControlUrl, detectAclModel, buildAcpAcr } from './acl.js';
import { publicReadAclBody } from './podio.js';

export const SAFE_ID_RE = /^[\w-]{1,128}$/;
// Foreign Long Chat reads are on attacker-supplied URLs and bodies: bound how
// large a day file we parse.
export const MAX_CHAT_DAY_BYTES = 512 * 1024;
const MAX_POD_JSON_BYTES = 65536;
const MAX_POD_LISTING_BYTES = 512 * 1024;

function _sameOrigin(a, b) {
    try { return new URL(a).origin === new URL(b).origin; } catch { return false; }
}

// A chat container URL must be an absolute http(s) URL ending in '/', with no
// fragment/query/whitespace or traversal. Mirrors solidchat.isValidChatContainer.
export function isValidChatContainer(url) {
    if (typeof url !== 'string' || !/^https?:\/\//.test(url) || !url.endsWith('/')) return false;
    if (/[\s?#]/.test(url) || url.includes('..')) return false;
    try { new URL(url); return true; } catch { return false; }
}

export function validateWebId(wid) {
    if (typeof wid !== 'string') return false;
    if (!wid.startsWith('https://')) return false;
    if (/[<>"{}|\\^`\s]/.test(wid)) return false;
    try {
        new URL(wid);
        return true;
    } catch {
        return false;
    }
}

export function buildWacAcl(ownerWebId, memberWebIds, containerUrl) {
    if (!validateWebId(ownerWebId)) throw new Error('Invalid owner WebID');
    const validMembers = (memberWebIds || []).filter(validateWebId);
    const memberLines = validMembers.map((wid) => `    acl:agent <${wid}>;`).join('\n');
    const memberBlock = validMembers.length > 0
        ? `\n<#members>\n    a acl:Authorization;\n${memberLines}\n    acl:accessTo <${containerUrl}>;\n    acl:default <${containerUrl}>;\n    acl:mode acl:Read.`
        : '';
    return (
        `@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n\n` +
        `<#owner>\n    a acl:Authorization;\n    acl:agent <${ownerWebId}>;\n` +
        `    acl:accessTo <${containerUrl}>;\n    acl:default <${containerUrl}>;\n` +
        `    acl:mode acl:Read, acl:Write, acl:Control.` +
        memberBlock + '\n'
    );
}

// ── Generic RDF PATCH (R101.3) ───────────────────────────────────────────────
// N3 Patch is the Solid-Protocol-mandated PATCH format (SPARQL Update is only
// recommended), so negotiate on the resource's Accept-Patch and prefer N3 Patch
// where advertised. `inserts`/`deletes`/`where` are arrays of triple strings.
export function buildSparqlUpdate({ inserts = [], deletes = [], where = [] }) {
    if (where.length) {
        const parts = [];
        if (deletes.length) parts.push(`DELETE {\n  ${deletes.join('\n  ')}\n}`);
        if (inserts.length) parts.push(`INSERT {\n  ${inserts.join('\n  ')}\n}`);
        parts.push(`WHERE {\n  ${where.join('\n  ')}\n}`);
        return parts.join('\n') + '\n';
    }
    const ops = [];
    if (deletes.length) ops.push(`DELETE DATA {\n  ${deletes.join('\n  ')}\n}`);
    if (inserts.length) ops.push(`INSERT DATA {\n  ${inserts.join('\n  ')}\n}`);
    return ops.join(' ;\n') + '\n';
}

export function buildN3Patch({ inserts = [], deletes = [], where = [] }) {
    const clauses = ['[] a solid:InsertDeletePatch'];
    if (where.length)   clauses.push(`  solid:where { ${where.join(' ')} }`);
    if (deletes.length) clauses.push(`  solid:deletes { ${deletes.join(' ')} }`);
    if (inserts.length) clauses.push(`  solid:inserts { ${inserts.join(' ')} }`);
    return `@prefix solid: <http://www.w3.org/ns/solid/terms#>.\n` + clauses.join(';\n') + '.\n';
}

/** Prefer N3 Patch when the server advertises it in Accept-Patch, else SPARQL. Pure. */
export function patchFormatFor(acceptPatch) {
    return (acceptPatch && /text\/n3/i.test(acceptPatch)) ? 'n3' : 'sparql';
}

export function createPodRoomIo({ fetch, isLoggedIn, storageRoot, webId, getSigner = () => null, fetchPeerSigner }) {
    const _loggedIn = () => { try { return !!isLoggedIn(); } catch { return false; } };
    const _root = () => storageRoot() || null;
    const _peerRootAllowed = (url) => isPeerPodRootAllowed(url, _root());   // SSRF gate

    async function _fetchJsonCapped(url, opts = {}, maxBytes = MAX_POD_JSON_BYTES) {
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

    // The access-control resource for a URL and the server's model, from the Link
    // header, falling back to the WAC '.acl' convention.
    async function discoverAccessControl(resourceUrl) {
        try {
            const res = await fetch(resourceUrl, { method: 'HEAD' });
            const link = (res && res.headers && res.headers.get) ? res.headers.get('link') : null;
            const url = accessControlUrl(link, resourceUrl);
            if (url) return { url, model: detectAclModel(link, url) || 'wac' };
        } catch (_) { /* fall through to the convention */ }
        return { url: resourceUrl + '.acl', model: 'wac' };
    }

    /** PATCH an RDF resource, negotiating N3 Patch vs SPARQL Update from Accept-Patch. */
    async function podRdfPatch(resourceUrl, ops) {
        let acceptPatch = null;
        try {
            const res = await fetch(resourceUrl, { method: 'HEAD', redirect: 'error' });
            acceptPatch = res?.headers?.get?.('accept-patch') || null;
        } catch (_) { /* default to SPARQL below */ }
        const fmt = patchFormatFor(acceptPatch);
        const body = fmt === 'n3' ? buildN3Patch(ops) : buildSparqlUpdate(ops);
        const ct = fmt === 'n3' ? 'text/n3' : 'application/sparql-update';
        // A PATCH is state-changing, so it must never be replayed against a host a peer
        // pod redirects us to; refuse the redirect the way the peer read paths do.
        return fetch(resourceUrl, {
            method: 'PATCH', headers: { 'Content-Type': ct }, body, redirect: 'error',
        });
    }

    // ── Public type index (R70 D) ──
    // A chat we host is Long-Chat-readable, but another app cannot FIND it without
    // the URL unless it is registered in the pod's public type index.

    /** The public type index IRI linked from ANY WebID's profile, or null. */
    async function podReadPublicTypeIndexUrlFor(wid) {
        if (!wid || !_loggedIn()) return null;
        try {
            const json = await _fetchJsonCapped(wid, { headers: { Accept: 'application/ld+json' }, redirect: 'error' }, MAX_POD_LISTING_BYTES);
            if (!json) return null;
            const url = parsePublicTypeIndex(json, wid);
            // The publicTypeIndex value is attacker-controlled even for a legit WebID, and
            // it is fetched next with our authenticated session; only follow one that is on
            // the profile's own origin and clears the SSRF gate.
            if (!url || !_sameOrigin(url, wid) || !_peerRootAllowed(url)) return null;
            return url;
        } catch (err) {
            console.warn('[pod] podReadPublicTypeIndexUrlFor failed:', err);
            return null;
        }
    }

    /**
     * Ensure a public type index exists and is linked from the WebID card; return its
     * URL (or null if we could not establish one). Best-effort: if the profile PATCH
     * is refused, we still return the index we created so our own reads/writes work,
     * but discovery by other apps needs the profile link.
     */
    async function podEnsurePublicTypeIndex() {
        const existing = await podReadPublicTypeIndexUrlFor(webId());
        if (existing) return existing;
        const root = _root();
        const me = webId();
        if (!root || !me) return null;
        const indexUrl = `${root}settings/publicTypeIndex.ttl`;
        try {
            // Create the index document (CSS creates intermediate containers on PUT).
            // Never over an existing one: a profile read that failed must not wipe
            // the registrations other apps made there.
            const put = await fetch(indexUrl, {
                method: 'PUT',
                headers: { 'Content-Type': 'text/turtle', 'If-None-Match': '*' },
                body: buildEmptyTypeIndex(),
            });
            const existed = !!(put && put.status === 412);
            if (!existed && (!put || !put.ok)) return null;
            // Make it PUBLIC-readable: it is the *public* type index, and discovery by
            // another app/person only works if they can actually read it. Owner keeps
            // full control. Without this the index is owner-only and discovery silently
            // fails cross-identity.
            if (!existed) try {
                const { url: idxAclUrl, model } = await discoverAccessControl(indexUrl);
                await fetch(idxAclUrl, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'text/turtle' },
                    body: publicReadAclBody(model, indexUrl, me),
                });
            } catch (err) {
                console.warn('[pod] type index public ACL failed:', err);
            }
            // Link it from the profile so other apps can discover it (R101.3: N3 Patch
            // when the server advertises it, else SPARQL Update).
            try {
                await podRdfPatch(me.split('#')[0], {
                    inserts: [`<${me}> <http://www.w3.org/ns/solid/terms#publicTypeIndex> <${indexUrl}> .`],
                });
            } catch (err) {
                console.warn('[pod] type index created but profile link failed:', err);
            }
            return indexUrl;
        } catch (err) {
            console.warn('[pod] podEnsurePublicTypeIndex failed:', err);
            return null;
        }
    }

    /** Register a chat container as a meeting:LongChat instance (discoverable). */
    async function podRegisterChat(containerUrl) {
        if (!containerUrl || !_loggedIn()) return false;
        const indexUrl = await podEnsurePublicTypeIndex();
        if (!indexUrl) return false;
        try {
            const res = await fetch(indexUrl, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/sparql-update' },
                body: buildRegisterPatch({ indexUrl, containerUrl }),
            });
            return !!(res && res.ok);
        } catch (err) {
            console.warn('[pod] podRegisterChat failed:', err);
            return false;
        }
    }

    /** Remove a chat container's registration from the public type index. */
    async function podDeregisterChat(containerUrl) {
        if (!containerUrl || !_loggedIn()) return false;
        const indexUrl = await podReadPublicTypeIndexUrlFor(webId());
        if (!indexUrl) return true;   // nothing linked, nothing to remove
        try {
            const res = await fetch(indexUrl, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/sparql-update' },
                body: buildDeregisterPatch({ indexUrl, containerUrl }),
            });
            return !!(res && res.ok);
        } catch (err) {
            console.warn('[pod] podDeregisterChat failed:', err);
            return false;
        }
    }

    /** Chat containers registered for meeting:LongChat in our public type index. */
    async function podListRegisteredChats() {
        const indexUrl = await podReadPublicTypeIndexUrlFor(webId());
        if (!indexUrl) return [];
        try {
            const json = await _fetchJsonCapped(indexUrl, { headers: { Accept: 'application/ld+json' }, redirect: 'error' }, MAX_POD_LISTING_BYTES);
            if (!json) return [];
            return parseRegisteredContainers(json);
        } catch (err) {
            console.warn('[pod] podListRegisteredChats failed:', err);
            return [];
        }
    }

    // ── Room descriptor (PLAN_ROUND_71 B1) ──

    /**
     * Write the canonical room descriptor to rooms/{id}/room.json. Fills in the
     * Long Chat pointer from the room id when the caller omitted it, so the
     * descriptor points at the same container the type index registers.
     */
    async function podWriteRoomDescriptor(descriptor) {
        const roomId = descriptor && descriptor.room_id;
        if (!roomId || !SAFE_ID_RE.test(roomId) || !_loggedIn()) return false;
        const root = _root();
        if (!root) return false;
        const doc = { ...descriptor };
        if (!doc.long_chat) doc.long_chat = chatRootUrl(root, roomId);
        try {
            const res = await fetch(`${root}rooms/${roomId}/room.json`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(doc),
            });
            return !!(res && res.ok);
        } catch (err) {
            console.warn('[pod] podWriteRoomDescriptor failed:', err);
            return false;
        }
    }

    /** Read + parse the canonical room descriptor of a room we host, or null. */
    async function podReadRoomDescriptor(roomId) {
        if (!SAFE_ID_RE.test(roomId)) return null;
        const root = _root();
        if (!root || !_loggedIn()) return null;
        try {
            const res = await fetch(`${root}rooms/${roomId}/room.json`);
            if (!res || !res.ok) return null;
            const text = await res.text();
            if (text.length > 65536) return null;
            return parseRoomDescriptor(JSON.parse(text));
        } catch (err) {
            console.warn('[pod] podReadRoomDescriptor failed:', err);
            return null;
        }
    }

    /** Read a room descriptor from a specific owner's pod (not our own). */
    async function podReadRoomDescriptorAt(ownerPodRoot, roomId) {
        if (!roomId || !SAFE_ID_RE.test(roomId) || !ownerPodRoot || !_loggedIn()) return null;
        if (!_peerRootAllowed(ownerPodRoot)) return null;   // SSRF gate (defence in depth)
        try {
            const res = await fetch(`${ownerPodRoot.replace(/\/?$/, '/')}rooms/${roomId}/room.json`, { redirect: 'error' });
            if (!res || !res.ok) return null;
            const text = await res.text();
            if (text.length > 65536) return null;
            return parseRoomDescriptor(JSON.parse(text));
        } catch (err) {
            console.warn('[pod] podReadRoomDescriptorAt failed:', err);
            return null;
        }
    }

    /**
     * Enumerate the descriptors for rooms this user OWNS, by reading the public type
     * index for registered chats, mapping each container back to a room id, and
     * reading its descriptor. Bounded by the number of registered chats.
     */
    async function podListOwnedRoomDescriptors(myWebId) {
        if (!myWebId || !_loggedIn()) return [];
        const containers = await podListRegisteredChats();
        const out = [];
        for (const c of containers) {
            const roomId = roomIdFromChatContainer(c);
            if (!roomId) continue;
            const desc = await podReadRoomDescriptor(roomId);
            if (desc && desc.owner === myWebId) out.push(desc);
        }
        return out;
    }

    async function podSetContainerAcl(containerPath, ownerWebId, memberWebIds) {
        const root = _root();
        if (!root) return;
        const containerUrl = root + containerPath;
        if (!containerUrl.startsWith(root)) return;
        try {
            const { url, model } = await discoverAccessControl(containerUrl);
            // Route by the server's access-control model: ACP for ESS-style servers,
            // WAC otherwise (CSS/NSS).
            const body = model === 'acp'
                ? buildAcpAcr(ownerWebId, memberWebIds, containerUrl)
                : buildWacAcl(ownerWebId, memberWebIds, containerUrl);
            await fetch(url, {
                method: 'PUT',
                headers: { 'Content-Type': 'text/turtle' },
                body,
            });
        } catch (err) {
            console.warn('access-control write failed:', err);
        }
    }

    // ── Long Chat (PLAN_ROUND_67 phases B and C) ──

    /** Create a chat channel resource if it is not there yet. Idempotent. */
    async function ensureChatIndexAt(containerUrl, title) {
        const url = indexUrlAt(containerUrl);
        try {
            const head = await fetch(url, { method: 'HEAD', redirect: 'error' });
            if (head && head.ok) return true;
        } catch { /* treat as missing and try to create it */ }
        try {
            const res = await fetch(url, {
                method: 'PUT',
                headers: { 'Content-Type': 'text/turtle' },
                body: buildIndexTurtle(title || 'Proxion room'),
                redirect: 'error',   // never follow a peer redirect off the vetted host
            });
            const ok = !!(res && res.ok);
            // Track D: the index was just created, so this is a NEW chat in our pod.
            // Register it in the public type index (once, here) so other Solid apps can
            // discover it. Only chats in OUR pod (we can only register our own storage).
            const _ownRoot = _root();
            if (ok && _ownRoot && containerUrl.startsWith(_ownRoot)) {
                podRegisterChat(containerUrl).catch(() => {});
            }
            return ok;
        } catch (err) {
            console.warn('[pod] ensureChatIndexAt failed:', err);
            return false;
        }
    }

    /**
     * Append one message to a chat at an ARBITRARY container URL, in our own pod
     * or a friend's we have Append on. Appends via PATCH the way SolidOS does, so
     * two people writing the same UTC day do not clobber each other. Returns
     * { ok, status }: a 403 from a joined room's pod means we were removed.
     */
    async function podWriteChatMessageAtResult(containerUrl, messageId, msg) {
        if (!containerUrl || !_peerRootAllowed(containerUrl) || !_loggedIn()) return { ok: false, status: 0 };
        const timestamp = msg.timestamp || new Date().toISOString();
        await ensureChatIndexAt(containerUrl, msg.room_name);
        // R101.1: if this is a reply and we know the parent's timestamp, build the
        // parent's pod IRI so the append can add sioc:has_reply.
        let replyToIri = null;
        if (msg.reply_to_id && msg.reply_to_timestamp) {
            replyToIri = messageIriAt(containerUrl, msg.reply_to_id, msg.reply_to_timestamp);
        }
        const messageIri = messageIriAt(containerUrl, messageId, timestamp);
        // #4: sign the message's core fields (id, created, content, maker) with this
        // device's identity key so a reader can prove the author. Best-effort: with no
        // signer the message writes unsigned, valid Long Chat that simply cannot be
        // verified, the same as one written by another Solid chat app.
        let proof = null;
        const signer = getSigner();
        if (signer) {
            proof = await signLongChat(
                { id: messageIri, created: timestamp, content: msg.content || '', maker: msg.from_webid || '', from_display_name: msg.from_display_name || '' },
                signer.privKey, signer.signerDid,
            );
        }
        const ops = appendOps({
            channelIri: channelIriAt(containerUrl),
            messageIri,
            content: msg.content || '',
            createdIso: timestamp,
            makerIri: msg.from_webid || '',
            // Part B: the display name is emitted into the shared day file and
            // covered by the signature above, so a signed message's name cannot be spoofed.
            fromName: msg.from_display_name || '',
            replyToIri,
            proof,
        });
        try {
            const res = await podRdfPatch(dayFileAt(containerUrl, timestamp), ops);
            return { ok: !!(res && res.ok), status: (res && res.status) || 0, policy: !!(res && res.error) };
        } catch (err) {
            console.warn('[pod] podWriteChatMessageAt failed:', err);
            return { ok: false, status: 0 };
        }
    }

    async function podWriteChatMessageAt(containerUrl, messageId, msg) {
        return (await podWriteChatMessageAtResult(containerUrl, messageId, msg)).ok;
    }

    /**
     * Read one UTC day of a chat at an arbitrary container URL. Requested as JSON-LD
     * via content negotiation (every Solid server supports it), so no RDF parser has
     * to ship to the browser. Reads chats written by SolidOS and POD-CHAT too.
     */
    async function podReadChatDayAt(containerUrl, date, threadId = '', ownerWebId = null) {
        if (!containerUrl || !_peerRootAllowed(containerUrl) || !_loggedIn()) return [];
        try {
            const res = await fetch(dayFileAt(containerUrl, date), {
                headers: { Accept: 'application/ld+json' },
                redirect: 'error',   // never follow a peer redirect off the vetted host
            });
            if (!res || !res.ok) return [];
            // Foreign day file: bound the body before parsing.
            const len = Number(res.headers?.get?.('content-length'));
            if (Number.isFinite(len) && len > MAX_CHAT_DAY_BYTES) return [];
            let json;
            if (typeof res.text === 'function') {
                const text = await res.text();
                if (text.length > MAX_CHAT_DAY_BYTES) return [];
                json = JSON.parse(text);
            } else {
                json = await res.json();
            }
            // #4/#5: parse, then authenticate each message's sec:proofValue against the
            // maker's published signer (the same trust anchor a DM uses), and honour a
            // soft-delete only when its px:deleteProof verifies for the maker or, when
            // known, the room owner. Unsigned/foreign messages stay
            // sender_verified:false; an unauthenticated tombstone leaves the message
            // visible. Nothing is dropped.
            return verifyLongChatMessages(parseLongChatJsonLd(json, threadId), {
                fetchPeerSigner,
                peerPodRoot: peerPodRootFromWebId,
                ownerWebId,
            });
        } catch (err) {
            console.warn('[pod] podReadChatDayAt failed:', err);
            return [];
        }
    }

    /**
     * Read the last `days` UTC days of a Long Chat, oldest first. Walking back a
     * bounded window is deliberate: enumerating every YYYY/MM/DD container would be
     * a request storm on a long-lived chat.
     */
    async function podReadChatRecentAt(containerUrl, days = 7, threadId = '', ownerWebId = null) {
        if (!_peerRootAllowed(containerUrl)) return [];   // SSRF gate (defence in depth)
        const out = [];
        const seen = new Set();
        const today = Date.now();
        for (let i = Math.max(0, days - 1); i >= 0; i--) {
            const when = new Date(today - i * 86400000);
            for (const m of await podReadChatDayAt(containerUrl, when, threadId, ownerWebId)) {
                if (m.message_id && seen.has(m.message_id)) continue;
                if (m.message_id) seen.add(m.message_id);
                out.push(m);
            }
        }
        return out;
    }

    /**
     * Grant a set of participants Read + Append on a chat we host, by writing the
     * container ACL. Append-only (#4): a participant can add a message but not
     * overwrite or delete an existing one. Rewriting it without someone removes them.
     */
    async function podGrantChatParticipants(containerUrl, ownerWebId, participantWebIds) {
        if (!containerUrl || !_loggedIn()) return false;
        try {
            const { url: aclUrl, model } = await discoverAccessControl(containerUrl);
            // On an ACP server (ESS) the WAC turtle would not grant anything; author an
            // ACR that gives participants Read+Append instead.
            const body = model === 'acp'
                ? buildAcpAcr(ownerWebId, participantWebIds, containerUrl, 'acl:Read, acl:Append')
                : buildChatAcl(ownerWebId, participantWebIds, containerUrl);
            const res = await fetch(aclUrl, {
                method: 'PUT',
                headers: { 'Content-Type': 'text/turtle' },
                body,
            });
            return !!(res && res.ok);
        } catch (err) {
            console.warn('[pod] podGrantChatParticipants failed:', err);
            return false;
        }
    }

    /**
     * The member list a room's owner keeps next to its Long Chat (members.jsonld in
     * the chat container), readable by every participant. For display only: members
     * hold Append on that container, so this is not an authority for access.
     * Returns [{ webid, role }], or null when it cannot be read.
     */
    async function podReadRoomMembersAt(containerUrl) {
        if (!isValidChatContainer(containerUrl) || !_peerRootAllowed(containerUrl) || !_loggedIn()) return null;
        try {
            const doc = await _fetchJsonCapped(containerUrl + 'members.jsonld',
                { headers: { Accept: 'application/ld+json' }, redirect: 'error' }, MAX_POD_JSON_BYTES);
            const raw = doc && doc['px:members'];
            if (!Array.isArray(raw)) return null;
            const out = [];
            const seen = new Set();
            for (const m of raw.slice(0, 500)) {
                const wid = m && typeof m.webid === 'string' ? m.webid : '';
                if (!/^https?:\/\//.test(wid) || seen.has(wid)) continue;
                seen.add(wid);
                out.push({ webid: wid, role: typeof m.role === 'string' ? m.role : 'member' });
            }
            return out;
        } catch (err) {
            console.warn('[pod] podReadRoomMembersAt failed:', err);
            return null;
        }
    }

    /** Write the member list of a room we host (members.jsonld in its chat container). */
    async function podWriteRoomMembers(roomId, members) {
        const root = _root();
        if (!root || !_loggedIn()) return;
        try {
            await fetch(`${root}proxion/rooms/${roomId}/members.jsonld`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/ld+json' },
                body: JSON.stringify({
                    '@context': { px: 'https://proxion.dev/vocab/v1#' },
                    '@type': 'px:MemberList',
                    'px:roomId': roomId,
                    'px:members': members,
                    'px:updatedAt': new Date().toISOString(),
                }),
            });
        } catch (err) {
            console.warn('[pod] podWriteRoomMembers failed:', err);
        }
    }

    return {
        discoverAccessControl, podRdfPatch,
        podReadPublicTypeIndexUrlFor, podEnsurePublicTypeIndex,
        podRegisterChat, podDeregisterChat, podListRegisteredChats,
        podWriteRoomDescriptor, podReadRoomDescriptor, podReadRoomDescriptorAt,
        podListOwnedRoomDescriptors, podSetContainerAcl,
        ensureChatIndexAt, podWriteChatMessageAt, podWriteChatMessageAtResult,
        podReadChatDayAt, podReadChatRecentAt, podGrantChatParticipants,
        podReadRoomMembersAt, podWriteRoomMembers,
    };
}
