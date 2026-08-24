import { solidSession, podStorageRoot } from './auth.js';
import {
    chatRootUrl, indexUrlAt, channelIriAt, dayFileAt, messageIriAt,
    buildIndexTurtle, appendOps, editOps, deleteOps, seqOps,
    buildChatAcl, roomIdFromChatContainer,
    parseLongChatJsonLd, mergeLongChatMessages, reactionActionTriples,
} from './longchat.js';
import {
    buildEmptyTypeIndex, buildRegisterPatch, buildDeregisterPatch,
    parsePublicTypeIndex, parseRegisteredContainers,
} from './typeindex.js';
import { parseRoomDescriptor } from './roomdesc.js';
import {
    buildInviteNotification, parseInboxListing, parseInviteNotification, INBOX_PRED,
} from './ldn.js';
import { accessControlUrl, detectAclModel, buildAcpAcr } from './acl.js';

const SAFE_ID_RE = /^[\w-]{1,128}$/;

async function podFetch(path, options = {}) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const url = root + path;
    try {
        await solidSession.fetch(url, options);
    } catch (err) {
        console.warn('pod write failed:', url, err);
    }
}

function _validateWebId(wid) {
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

async function _fetchOnePodMessage(url) {
    try {
        const r = await solidSession.fetch(url);
        if (!r.ok) return null;
        const ct = r.headers.get('content-type') || '';
        if (!ct.includes('json')) return null;
        const text = await r.text();
        if (text.length > 65536) return null;
        const msg = JSON.parse(text);
        if (
            typeof msg?.message_id !== 'string' ||
            typeof msg?.content !== 'string' ||
            typeof msg?.timestamp !== 'string' ||
            typeof msg?.from_webid !== 'string'
        ) return null;
        if (!SAFE_ID_RE.test(msg.message_id)) return null;
        return msg;
    } catch {
        return null;
    }
}

export async function podWriteMessage(roomId, msg) {
    if (!SAFE_ID_RE.test(roomId) || !SAFE_ID_RE.test(msg?.message_id || '')) return;
    await podFetch(
        `rooms/${roomId}/messages/${msg.message_id}.json`,
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(msg),
        }
    );
}

export async function podWriteMessageWithIndex(roomId, msg) {
    if (!SAFE_ID_RE.test(roomId) || !SAFE_ID_RE.test(msg?.message_id || '')) return;
    await podWriteMessage(roomId, msg);
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const indexUrl = root + `rooms/${roomId}/messages/index.json`;
    try {
        let ids = [];
        const res = await solidSession.fetch(indexUrl);
        if (res.ok) {
            const raw = await res.json();
            ids = Array.isArray(raw?.ids)
                ? raw.ids.filter((id) => typeof id === 'string' && SAFE_ID_RE.test(id))
                : [];
        }
        if (!ids.includes(msg.message_id)) {
            ids.push(msg.message_id);
            if (ids.length > 10000) ids = ids.slice(-10000);
            await solidSession.fetch(indexUrl, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids }),
            });
        }
    } catch (err) {
        console.warn('pod index update failed:', err);
    }
}

export async function podWriteRoomMeta(roomId, meta) {
    if (!SAFE_ID_RE.test(roomId)) return;
    await podFetch(
        `rooms/${roomId}/room.json`,
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(meta),
        }
    );
}

/**
 * Write the canonical room descriptor (PLAN_ROUND_71 B1) to rooms/{id}/room.json.
 * Fills in the Long Chat pointer from the room id when the caller omitted it, so
 * the descriptor points at the same container the type index registers. Returns
 * success (unlike the best-effort podWriteRoomMeta) so callers/tests can react.
 */
export async function podWriteRoomDescriptor(descriptor) {
    const roomId = descriptor && descriptor.room_id;
    if (!roomId || !SAFE_ID_RE.test(roomId) || !solidSession?.info?.isLoggedIn) return false;
    const root = podStorageRoot();
    if (!root) return false;
    const doc = { ...descriptor };
    if (!doc.long_chat) doc.long_chat = chatRootUrl(root, roomId);
    try {
        const res = await solidSession.fetch(`${root}rooms/${roomId}/room.json`, {
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

/**
 * Enumerate the descriptors for rooms this user OWNS, by reading the public type
 * index (R70 D) for registered chats, mapping each container back to a room id, and
 * reading its descriptor. This is how the client finds which rooms to rehydrate to
 * a gateway that has lost them (B2). Bounded by the number of registered chats.
 */
export async function podListOwnedRoomDescriptors(myWebId) {
    if (!myWebId || !solidSession?.info?.isLoggedIn) return [];
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

/** Read + parse the canonical room descriptor, or null. */
export async function podReadRoomDescriptor(roomId) {
    if (!SAFE_ID_RE.test(roomId)) return null;
    const root = podStorageRoot();
    if (!root || !solidSession?.info?.isLoggedIn) return null;
    try {
        const res = await solidSession.fetch(`${root}rooms/${roomId}/room.json`);
        if (!res || !res.ok) return null;
        const text = await res.text();
        if (text.length > 65536) return null;
        return parseRoomDescriptor(JSON.parse(text));
    } catch (err) {
        console.warn('[pod] podReadRoomDescriptor failed:', err);
        return null;
    }
}

export async function podReadMessages(roomId) {
    if (!SAFE_ID_RE.test(roomId)) return [];
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return [];
    const indexUrl = root + `rooms/${roomId}/messages/index.json`;
    try {
        const res = await solidSession.fetch(indexUrl);
        if (res.status === 404 || !res.ok) return [];
        const raw = await res.json();
        if (!raw || !Array.isArray(raw.ids)) return [];
        const ids = raw.ids.filter((id) => typeof id === 'string' && SAFE_ID_RE.test(id));
        const limited = ids.slice(-200);
        const results = await Promise.allSettled(
            limited.map((id) => _fetchOnePodMessage(root + `rooms/${roomId}/messages/${id}.json`))
        );
        const msgs = results
            .filter((r) => r.status === 'fulfilled' && r.value)
            .map((r) => r.value);
        msgs.sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''));
        return msgs;
    } catch {
        return [];
    }
}

export async function podReadRoomMeta(roomId) {
    if (!SAFE_ID_RE.test(roomId)) return null;
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return null;
    try {
        const res = await solidSession.fetch(root + `rooms/${roomId}/room.json`);
        if (!res.ok) return null;
        const text = await res.text();
        if (text.length > 65536) return null;
        const meta = JSON.parse(text);
        if (typeof meta?.room_id !== 'string') return null;
        return meta;
    } catch {
        return null;
    }
}

function buildWacAcl(ownerWebId, memberWebIds, containerUrl) {
    if (!_validateWebId(ownerWebId)) throw new Error('Invalid owner WebID');
    const validMembers = (memberWebIds || []).filter(_validateWebId);
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

/**
 * Discover a resource's access-control resource URL and model from its `Link`
 * header (R100 A2), falling back to the `.acl` convention + WAC when the server
 * advertises nothing (e.g. the resource does not exist yet, or is CSS/NSS). On
 * CSS this returns the same `${url}.acl` it always did, so behaviour is unchanged;
 * on servers that advertise a different ACL/ACR location it targets the right one.
 */
export async function discoverAccessControl(resourceUrl) {
    try {
        const res = await solidSession.fetch(resourceUrl, { method: 'HEAD' });
        const link = (res && res.headers && res.headers.get) ? res.headers.get('link') : null;
        const url = accessControlUrl(link, resourceUrl);
        if (url) return { url, model: detectAclModel(link, url) || 'wac' };
    } catch (_) { /* fall through to the convention */ }
    return { url: resourceUrl + '.acl', model: 'wac' };
}

export async function podSetContainerAcl(containerPath, ownerWebId, memberWebIds) {
    const root = podStorageRoot();
    if (!root) return;
    const containerUrl = root + containerPath;
    if (!containerUrl.startsWith(root)) return;
    try {
        const { url, model } = await discoverAccessControl(containerUrl);
        // Route by the server's access-control model: ACP for ESS-style servers,
        // WAC otherwise (CSS/NSS). ACP authoring is unverified against live ESS
        // (R100 A2.2); it only runs when the server advertises ACP, so it cannot
        // affect WAC servers.
        const body = model === 'acp'
            ? buildAcpAcr(ownerWebId, memberWebIds, containerUrl)
            : buildWacAcl(ownerWebId, memberWebIds, containerUrl);
        await solidSession.fetch(url, {
            method: 'PUT',
            headers: { 'Content-Type': 'text/turtle' },
            body,
        });
    } catch (err) {
        console.warn('access-control write failed:', err);
    }
}

// --- Bootstrap ---

export async function ensureProxionContainer() {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const uri = `${root}proxion/`;
    try {
        const res = await solidSession.fetch(uri, { method: 'HEAD' });
        if (res.status !== 404) return;
        await solidSession.fetch(uri, {
            method: 'PUT',
            headers: { 'Content-Type': 'text/turtle' },
            body: '',
        });
        const { url: aclUrl } = await discoverAccessControl(uri);
        await solidSession.fetch(aclUrl, {
            method: 'PUT',
            headers: { 'Content-Type': 'text/turtle' },
            // acl:default, NOT acl:defaultForNew. The latter is a deprecated
            // predicate that current servers ignore, which grants access to this
            // container but to nothing inside it, so every subsequent write under
            // proxion/ fails with 403. Verified against CSS 7.1.9.
            body: `@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n<#owner> a acl:Authorization;\n    acl:agent <${solidSession.info.webId}>;\n    acl:accessTo <${uri}>;\n    acl:default <${uri}>;\n    acl:mode acl:Read, acl:Write, acl:Control.`,
        });
    } catch (err) {
        console.warn('[pod] ensureProxionContainer failed:', err);
    }
}

// --- Profile ---

export async function podWriteProfile({ displayName, avatarBlob } = {}) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        if (displayName !== undefined) {
            await solidSession.fetch(`${root}proxion/profile/display_name.jsonld`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/ld+json' },
                body: JSON.stringify({
                    '@context': { px: 'https://proxion.dev/vocab/v1#' },
                    '@type': 'px:Profile',
                    'px:displayName': displayName,
                    'px:updatedAt': new Date().toISOString(),
                }),
            });
        }
        if (avatarBlob) {
            await solidSession.fetch(`${root}proxion/profile/avatar.png`, {
                method: 'PUT',
                headers: { 'Content-Type': avatarBlob.type || 'image/png' },
                body: avatarBlob,
            });
        }
    } catch (err) {
        console.warn('[pod] podWriteProfile failed:', err);
    }
}

export async function podReadProfile() {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return null;
    try {
        const res = await solidSession.fetch(`${root}proxion/profile/display_name.jsonld`,
            { headers: { Accept: 'application/ld+json' } });
        if (!res.ok) return null;
        return res.json();
    } catch {
        return null;
    }
}

// --- Messages (canonical JSON-LD) ---

export async function podWriteMessageJsonLd(threadId, messageId, msg, isRoom = true) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return false;
    const dir = isRoom ? `rooms/${threadId}` : `dm/${threadId}`;
    const uri = `${root}proxion/${dir}/messages/${messageId}.jsonld`;
    const timestamp = msg.timestamp || new Date().toISOString();
    const doc = {
        '@context': { px: 'https://proxion.dev/vocab/v1#' },
        '@type': 'px:Message',
        '@id': uri,
        'px:messageId': messageId,
        'px:threadId': threadId,
        'px:content': msg.content || '',
        'px:contentType': msg.content_type || 'text',
        'px:fromWebid': msg.from_webid || '',
        'px:fromName': msg.from_display_name || '',
        'px:timestamp': timestamp,
        'px:replyToId': msg.reply_to_id || null,
        'px:replyToSnippet': msg.reply_to_snippet || null,
        'px:forwarded': msg.forwarded || false,
        'px:forwardedFromName': msg.forwarded_from_name || null,
    };
    if (isRoom) applyLongChatTerms(doc, msg, timestamp);
    // Report success (D2 write-through): the send path tracks this and surfaces a
    // retry if the pod write does not land, instead of the old silent swallow.
    let pxOk = false;
    try {
        const res = await solidSession.fetch(uri, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify(doc),
        });
        pxOk = !!(res && res.ok);
    } catch (err) {
        console.warn('[pod] podWriteMessageJsonLd failed:', err);
    }
    // Rooms are additionally written in the Long Chat container layout so other
    // Solid apps can open them. For a room this IS the durable log, so its write
    // counts toward "saved to the pod"; for a DM there is no Long Chat.
    let lcOk = true;
    if (isRoom) {
        lcOk = await podWriteLongChatMessage(threadId, messageId, { ...msg, timestamp });
    }
    return pxOk && lcOk;
}

// ── Long Chat layout (PLAN_ROUND_67 phases B and C) ──────────────────────────

/** Create a chat channel resource if it is not there yet. Idempotent. */
async function ensureChatIndexAt(containerUrl, title) {
    const url = indexUrlAt(containerUrl);
    try {
        const head = await solidSession.fetch(url, { method: 'HEAD' });
        if (head && head.ok) return true;
    } catch { /* treat as missing and try to create it */ }
    try {
        const res = await solidSession.fetch(url, {
            method: 'PUT',
            headers: { 'Content-Type': 'text/turtle' },
            body: buildIndexTurtle(title || 'Proxion room'),
        });
        const ok = !!(res && res.ok);
        // Track D: the index was just created, so this is a NEW chat in our pod.
        // Register it in the public type index (once, here) so other Solid apps can
        // discover it. Container-addressed, so this covers both rooms and shared
        // chats. Best-effort and only for chats in OUR pod (we can only register
        // discoverable instances of our own storage).
        const _ownRoot = podStorageRoot();
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
 * Append one message to a chat at an ARBITRARY container URL. This is the
 * cross-app primitive: the container may be in our own pod OR in a friend's pod
 * we have write access to. Appends via SPARQL-Update PATCH the way SolidOS does,
 * so two people writing the same UTC day do not clobber each other. Verified
 * against a live pod with a second identity posting to another user's chat.
 */
export async function podWriteChatMessageAt(containerUrl, messageId, msg) {
    if (!containerUrl || !solidSession?.info?.isLoggedIn) return false;
    const timestamp = msg.timestamp || new Date().toISOString();
    await ensureChatIndexAt(containerUrl, msg.room_name);
    // R101.1: if this is a reply and we know the parent's timestamp (threaded from
    // the client, which has it locally), build the parent's pod IRI so the append
    // can add sioc:has_reply. Message IRIs are date-partitioned, so the parent
    // timestamp is required; without it we simply omit the standard reply link.
    let replyToIri = null;
    if (msg.reply_to_id && msg.reply_to_timestamp) {
        replyToIri = messageIriAt(containerUrl, msg.reply_to_id, msg.reply_to_timestamp);
    }
    const ops = appendOps({
        channelIri: channelIriAt(containerUrl),
        messageIri: messageIriAt(containerUrl, messageId, timestamp),
        content: msg.content || '',
        createdIso: timestamp,
        makerIri: msg.from_webid || '',
        replyToIri,
    });
    try {
        const res = await podRdfPatch(dayFileAt(containerUrl, timestamp), ops);
        return !!(res && res.ok);
    } catch (err) {
        console.warn('[pod] podWriteChatMessageAt failed:', err);
        return false;
    }
}

/**
 * Read one UTC day of a chat at an arbitrary container URL. Requested as JSON-LD
 * via content negotiation (every Solid server supports it), so no RDF parser has
 * to ship to the browser. Reads chats written by SolidOS and POD-CHAT too.
 */
export async function podReadChatDayAt(containerUrl, date, threadId = '') {
    if (!containerUrl || !solidSession?.info?.isLoggedIn) return [];
    try {
        const res = await solidSession.fetch(dayFileAt(containerUrl, date), {
            headers: { Accept: 'application/ld+json' },
        });
        if (!res || !res.ok) return [];
        return parseLongChatJsonLd(await res.json(), threadId);
    } catch (err) {
        console.warn('[pod] podReadChatDayAt failed:', err);
        return [];
    }
}

/**
 * Rewrite a message's text in a chat's Long Chat day file (Phase B: edits). The
 * message lives in the day file for its ORIGINAL send date, so `date` must be the
 * message's own timestamp, not now. Best-effort, like the write path.
 */
export async function podEditChatMessageAt(containerUrl, messageId, date, newContent) {
    if (!containerUrl || !solidSession?.info?.isLoggedIn) return false;
    const ops = editOps({
        messageIri: messageIriAt(containerUrl, messageId, date),
        newContent: newContent || '',
    });
    try {
        const res = await podRdfPatch(dayFileAt(containerUrl, date), ops);
        return !!(res && res.ok);
    } catch (err) {
        console.warn('[pod] podEditChatMessageAt failed:', err);
        return false;
    }
}

/**
 * Soft-delete a message in a chat's Long Chat day file (Phase B: deletes) by
 * appending a schema:dateDeleted tombstone. `date` is the message's own send
 * date (which day file it lives in), not now.
 */
export async function podSoftDeleteChatMessageAt(containerUrl, messageId, date, deletedIso) {
    if (!containerUrl || !solidSession?.info?.isLoggedIn) return false;
    const ops = deleteOps({
        messageIri: messageIriAt(containerUrl, messageId, date),
        deletedIso: deletedIso || new Date().toISOString(),
    });
    try {
        const res = await podRdfPatch(dayFileAt(containerUrl, date), ops);
        return !!(res && res.ok);
    } catch (err) {
        console.warn('[pod] podSoftDeleteChatMessageAt failed:', err);
        return false;
    }
}

/**
 * Stamp the D4 order hint (px:seq) onto a message already in a chat's day file.
 * Used on the gateway echo, once the server's single-clock order is known, so a
 * user's devices agree on order despite client clock skew. `date` locates the day
 * file; the server and client send times share a UTC day in all but the rare
 * midnight-boundary case, where this simply no-ops and timestamp order stands.
 */
export async function podSetChatSeqAt(containerUrl, messageId, date, seq) {
    if (!containerUrl || !solidSession?.info?.isLoggedIn || !Number.isFinite(seq)) return false;
    const ops = seqOps({ messageIri: messageIriAt(containerUrl, messageId, date), seq });
    if (!ops.inserts.length) return false;
    try {
        const res = await podRdfPatch(dayFileAt(containerUrl, date), ops);
        return !!(res && res.ok);
    } catch (err) {
        console.warn('[pod] podSetChatSeqAt failed:', err);
        return false;
    }
}

/**
 * Grant a set of participants the ability to POST to a chat we host, by writing
 * the container ACL (owner control + participants read/write/append). This is
 * what turns "a chat in my pod" into "a conversation others can take part in".
 */
export async function podGrantChatParticipants(containerUrl, ownerWebId, participantWebIds) {
    if (!containerUrl || !solidSession?.info?.isLoggedIn) return false;
    try {
        const { url: aclUrl } = await discoverAccessControl(containerUrl);
        const res = await solidSession.fetch(aclUrl, {
            method: 'PUT',
            headers: { 'Content-Type': 'text/turtle' },
            body: buildChatAcl(ownerWebId, participantWebIds, containerUrl),
        });
        return !!(res && res.ok);
    } catch (err) {
        console.warn('[pod] podGrantChatParticipants failed:', err);
        return false;
    }
}

// ── Self-pod wrappers (a chat WE host under proxion/rooms/{roomId}/) ──────────

export function podWriteLongChatMessage(roomId, messageId, msg) {
    const root = podStorageRoot();
    if (!root) return Promise.resolve(false);
    return podWriteChatMessageAt(chatRootUrl(root, roomId), messageId, msg);
}

export function podReadLongChatDay(roomId, date) {
    const root = podStorageRoot();
    if (!root) return Promise.resolve([]);
    return podReadChatDayAt(chatRootUrl(root, roomId), date, roomId);
}

export function podEditLongChatMessage(roomId, messageId, date, newContent) {
    const root = podStorageRoot();
    if (!root) return Promise.resolve(false);
    return podEditChatMessageAt(chatRootUrl(root, roomId), messageId, date, newContent);
}

export function podSoftDeleteLongChatMessage(roomId, messageId, date, deletedIso) {
    const root = podStorageRoot();
    if (!root) return Promise.resolve(false);
    return podSoftDeleteChatMessageAt(chatRootUrl(root, roomId), messageId, date, deletedIso);
}

export function podSetLongChatSeq(roomId, messageId, date, seq) {
    const root = podStorageRoot();
    if (!root) return Promise.resolve(false);
    return podSetChatSeqAt(chatRootUrl(root, roomId), messageId, date, seq);
}

/**
 * Read the last `days` UTC days of a Long Chat, oldest first.
 *
 * Walking back a bounded window is deliberate: enumerating every YYYY/MM/DD
 * container to find all history would be a request storm on a long-lived chat.
 * A caller wanting more history pages further back by raising `days`.
 */
export async function podReadChatRecentAt(containerUrl, days = 7, threadId = '') {
    const out = [];
    const seen = new Set();
    const today = Date.now();
    for (let i = Math.max(0, days - 1); i >= 0; i--) {
        const when = new Date(today - i * 86400000);
        for (const m of await podReadChatDayAt(containerUrl, when, threadId)) {
            if (m.message_id && seen.has(m.message_id)) continue;
            if (m.message_id) seen.add(m.message_id);
            out.push(m);
        }
    }
    return out;
}

export function podReadLongChatRecent(roomId, days = 7) {
    const root = podStorageRoot();
    if (!root) return Promise.resolve([]);
    return podReadChatRecentAt(chatRootUrl(root, roomId), days, roomId);
}

// ── Pod-as-source-of-truth, first step (PLAN_ROUND_67 Phase D) ───────────────

export { mergeLongChatMessages, reconcileRoomHistory } from './longchat.js';

/**
 * Hydrate a room's history from its pod Long Chat and merge it with whatever is
 * held locally. This is the read half of "the pod is the source of truth": it
 * makes history written by another device, or by another Solid app entirely,
 * appear in Proxion.
 *
 * It deliberately does NOT demote the local store to a cache yet. Doing that
 * means write-through ordering, offline behaviour and conflict resolution, and
 * PLAN_ROUND_67 gates that on the format first being proven against a live pod.
 * So this returns a merged list for a caller to use, and changes nothing on its
 * own.
 */
export async function podHydrateRoom(roomId, { days = 7, local = [] } = {}) {
    const fromPod = await podReadLongChatRecent(roomId, days);
    return mergeLongChatMessages(local, fromPod);
}

// ── Type Index: make chats discoverable by other Solid apps (Track D) ─────────
//
// A chat we host is Long-Chat-readable, but another app cannot FIND it without the
// URL unless it is registered in the pod's public type index. These functions
// ensure the index exists and is linked from the WebID card, then register /
// deregister / list meeting:LongChat containers there.

/** The public type index IRI linked from ANY WebID's profile, or null. */
export async function podReadPublicTypeIndexUrlFor(webId) {
    if (!webId || !solidSession?.info?.isLoggedIn) return null;
    try {
        const res = await solidSession.fetch(webId, { headers: { Accept: 'application/ld+json' } });
        if (!res || !res.ok) return null;
        return parsePublicTypeIndex(await res.json(), webId);
    } catch (err) {
        console.warn('[pod] podReadPublicTypeIndexUrlFor failed:', err);
        return null;
    }
}

/** The public type index IRI linked from OUR WebID profile, or null. */
export function podReadPublicTypeIndexUrl() {
    return podReadPublicTypeIndexUrlFor(solidSession?.info?.webId);
}

/**
 * Ensure a public type index exists and is linked from the WebID card; return its
 * URL (or null if we could not establish one). Best-effort: if the profile PATCH
 * is refused, we still return the index we created so our own reads/writes work,
 * but discovery by other apps needs the profile link.
 */
export async function podEnsurePublicTypeIndex() {
    const existing = await podReadPublicTypeIndexUrl();
    if (existing) return existing;
    const root = podStorageRoot();
    const webId = solidSession?.info?.webId;
    if (!root || !webId) return null;
    const indexUrl = `${root}settings/publicTypeIndex.ttl`;
    try {
        // Create the index document (CSS creates intermediate containers on PUT).
        const put = await solidSession.fetch(indexUrl, {
            method: 'PUT',
            headers: { 'Content-Type': 'text/turtle' },
            body: buildEmptyTypeIndex(),
        });
        if (!put || !put.ok) return null;
        // Make it PUBLIC-readable: it is the *public* type index, and discovery by
        // another app/person only works if they can actually read it. Owner keeps
        // full control. Without this the index is owner-only and discovery silently
        // fails cross-identity.
        try {
            const { url: idxAclUrl } = await discoverAccessControl(indexUrl);
            await solidSession.fetch(idxAclUrl, {
                method: 'PUT',
                headers: { 'Content-Type': 'text/turtle' },
                body: `@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n@prefix foaf: <http://xmlns.com/foaf/0.1/>.\n`
                    + `<#owner> a acl:Authorization; acl:agent <${webId}>; acl:accessTo <${indexUrl}>; acl:mode acl:Read, acl:Write, acl:Control.\n`
                    + `<#public> a acl:Authorization; acl:agentClass foaf:Agent; acl:accessTo <${indexUrl}>; acl:mode acl:Read.\n`,
            });
        } catch (err) {
            console.warn('[pod] type index public ACL failed:', err);
        }
        // Link it from the profile so other apps can discover it (R101.3: N3 Patch
        // when the server advertises it, else SPARQL Update).
        try {
            await podRdfPatch(webId.split('#')[0], {
                inserts: [`<${webId}> <http://www.w3.org/ns/solid/terms#publicTypeIndex> <${indexUrl}> .`],
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

// ── WebID profile name (R100/A1, Solid WebID Profile v1.0.0) ─────────────────
// A did:key-only user has no dereferenceable card, but a pod-connected user does,
// and by default it carries no name, so other Solid apps (SolidOS, etc.) show an
// opaque id. Publish the user's Proxion display name as foaf:name + vcard:fn via a
// non-destructive SPARQL-update upsert. Best-effort, same idiom as the type-index
// profile link above. (_FOAF_NAME / _VCARD_FN are declared once in the contacts
// section below and reused here.)
function _sparqlLiteral(s) {
    return '"' + String(s)
        .replace(/\\/g, '\\\\').replace(/"/g, '\\"')
        .replace(/\n/g, '\\n').replace(/\r/g, '\\r') + '"';
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

/** PATCH an RDF resource, negotiating N3 Patch vs SPARQL Update from Accept-Patch. */
export async function podRdfPatch(resourceUrl, ops) {
    let acceptPatch = null;
    try {
        const res = await solidSession.fetch(resourceUrl, { method: 'HEAD' });
        acceptPatch = res?.headers?.get?.('accept-patch') || null;
    } catch (_) { /* default to SPARQL below */ }
    const fmt = patchFormatFor(acceptPatch);
    const body = fmt === 'n3' ? buildN3Patch(ops) : buildSparqlUpdate(ops);
    const ct = fmt === 'n3' ? 'text/n3' : 'application/sparql-update';
    return solidSession.fetch(resourceUrl, {
        method: 'PATCH', headers: { 'Content-Type': ct }, body,
    });
}

/** The foaf:name + vcard:fn triples to set (and, if replacing, delete). */
function _profileNameOps(webId, name, prevName) {
    const w = `<${webId}>`;
    const lit = _sparqlLiteral(name);
    const inserts = [`${w} <${_FOAF_NAME}> ${lit} .`, `${w} <${_VCARD_FN}> ${lit} .`];
    const deletes = prevName
        ? [`${w} <${_FOAF_NAME}> ${_sparqlLiteral(prevName)} .`, `${w} <${_VCARD_FN}> ${_sparqlLiteral(prevName)} .`]
        : [];
    return { inserts, deletes };
}

/** SPARQL-update form of the profile-name patch (kept for callers/tests). Pure. */
export function buildProfileNamePatch({ webId, name, prevName }) {
    return buildSparqlUpdate(_profileNameOps(webId, name, prevName));
}

/** Read the current foaf:name for webId from a JSON-LD card, or null. Pure. */
export function extractFoafName(json, webId) {
    const nodes = Array.isArray(json)
        ? json
        : (Array.isArray(json?.['@graph']) ? json['@graph'] : [json]);
    for (const n of nodes) {
        if (!n) continue;
        const id = n['@id'];
        if (id !== webId && !(typeof id === 'string' && id.endsWith('#me'))) continue;
        const v = n[_FOAF_NAME] ?? n['foaf:name'] ?? n.name;
        if (v == null) continue;
        const first = Array.isArray(v) ? v[0] : v;
        if (first && typeof first === 'object') return first['@value'] ?? null;
        if (typeof first === 'string') return first;
    }
    return null;
}

/** Upsert the pod WebID card's name to displayName. Best-effort; no-op when not
 *  pod-connected or the name is already correct. */
export async function podEnsureProfileName(displayName) {
    const webId = solidSession?.info?.webId;
    if (!webId || !displayName || !solidSession?.info?.isLoggedIn) return false;
    let prevName = null;
    try {
        const res = await solidSession.fetch(webId, { headers: { Accept: 'application/ld+json' } });
        if (res && res.ok) prevName = extractFoafName(await res.json(), webId);
    } catch (_) { /* card unreadable: fall through to a plain insert */ }
    if (prevName === displayName) return true;
    try {
        const r = await podRdfPatch(webId.split('#')[0], _profileNameOps(webId, displayName, prevName));
        return !!(r && r.ok);
    } catch (err) {
        console.warn('[pod] profile name patch failed:', err);
        return false;
    }
}

/** Register a chat container as a meeting:LongChat instance (discoverable). */
export async function podRegisterChat(containerUrl) {
    if (!containerUrl || !solidSession?.info?.isLoggedIn) return false;
    const indexUrl = await podEnsurePublicTypeIndex();
    if (!indexUrl) return false;
    try {
        const res = await solidSession.fetch(indexUrl, {
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
export async function podDeregisterChat(containerUrl) {
    if (!containerUrl || !solidSession?.info?.isLoggedIn) return false;
    const indexUrl = await podReadPublicTypeIndexUrl();
    if (!indexUrl) return true;   // nothing linked, nothing to remove
    try {
        const res = await solidSession.fetch(indexUrl, {
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
export async function podListRegisteredChats() {
    const indexUrl = await podReadPublicTypeIndexUrl();
    if (!indexUrl) return [];
    try {
        const res = await solidSession.fetch(indexUrl, { headers: { Accept: 'application/ld+json' } });
        if (!res || !res.ok) return [];
        return parseRegisteredContainers(await res.json());
    } catch (err) {
        console.warn('[pod] podListRegisteredChats failed:', err);
        return [];
    }
}

/**
 * Discover the chats a given WebID hosts (PLAN_ROUND_74 F1). Reads that WebID's
 * public type index for meeting:LongChat containers, then best-effort reads each
 * chat's index.ttl for a friendly dc:title (falling back to the room id). Read-only
 * and permission-respecting: only what the target pod's ACLs allow is returned.
 * Returns [{ container, title }].
 */
export async function podListChatsForWebId(webId) {
    if (!webId || !solidSession?.info?.isLoggedIn) return [];
    const indexUrl = await podReadPublicTypeIndexUrlFor(webId);
    if (!indexUrl) return [];
    let containers = [];
    try {
        const res = await solidSession.fetch(indexUrl, { headers: { Accept: 'application/ld+json' } });
        if (!res || !res.ok) return [];
        containers = parseRegisteredContainers(await res.json());
    } catch (err) {
        console.warn('[pod] podListChatsForWebId failed:', err);
        return [];
    }
    const out = [];
    for (const container of containers) {
        out.push({ container, title: await _readChatTitle(container) });
    }
    return out;
}

// Best-effort dc:title of a chat, from its index.ttl; falls back to the room id.
async function _readChatTitle(container) {
    const fallback = roomIdFromChatContainer(container) || container;
    try {
        const res = await solidSession.fetch(indexUrlAt(container), { headers: { Accept: 'text/turtle' } });
        if (!res || !res.ok) return fallback;
        const ttl = await res.text();
        const m = ttl.match(/(?:dc:title|<http:\/\/purl\.org\/dc\/elements\/1\.1\/title>)\s+"([^"]*)"/);
        return (m && m[1]) ? m[1] : fallback;
    } catch {
        return fallback;
    }
}

// Self-pod wrapper: register the room we host under proxion/rooms/{roomId}/.
export function podRegisterRoomChat(roomId) {
    const root = podStorageRoot();
    if (!root) return Promise.resolve(false);
    return podRegisterChat(chatRootUrl(root, roomId));
}

export function podDeregisterRoomChat(roomId) {
    const root = podStorageRoot();
    if (!root) return Promise.resolve(false);
    return podDeregisterChat(chatRootUrl(root, roomId));
}

// ── Solid inbox (Linked Data Notifications) — cross-app chat invites ──────────
//
// LDN (W3C Rec): a WebID profile advertises an ldp:inbox; anyone may POST a
// notification there; only the owner reads/lists/deletes. We use it to send and
// receive chat invitations that any Solid app can produce or consume.

/** The ldp:inbox IRI advertised by a WebID's profile, or null. */
export async function podDiscoverInbox(webId) {
    if (!webId || !solidSession?.info?.isLoggedIn) return null;
    try {
        const res = await solidSession.fetch(webId, { headers: { Accept: 'application/ld+json' } });
        if (!res || !res.ok) return null;
        const json = await res.json();
        for (const node of _jsonldNodes(json)) {
            const [inbox] = _idsOf(node, INBOX_PRED);
            if (inbox) return inbox;
        }
        return null;
    } catch (err) {
        console.warn('[pod] podDiscoverInbox failed:', err);
        return null;
    }
}

/**
 * Ensure OUR inbox exists, is public-Append (the LDN norm: others may drop a
 * notification, only we read/delete), and is linked from our profile so others
 * can find it. Best-effort; returns the inbox URL or null.
 */
export async function podEnsureInbox() {
    const existing = await podDiscoverInbox(solidSession?.info?.webId);
    if (existing) return existing;
    const root = podStorageRoot();
    const webId = solidSession?.info?.webId;
    if (!root || !webId) return null;
    const inboxUrl = `${root}inbox/`;
    try {
        // Create the container (LDP: PUT with a BasicContainer type link).
        const put = await solidSession.fetch(inboxUrl, {
            method: 'PUT',
            headers: {
                'Content-Type': 'text/turtle',
                Link: '<http://www.w3.org/ns/ldp#BasicContainer>; rel="type"',
            },
            body: '',
        });
        if (!put || !(put.ok || put.status === 409 || put.status === 405)) return null;
        // Public-Append ACL: owner full control (and default over children so we can
        // read/delete the notifications inside); everyone else may only Append.
        try {
            const { url: inboxAclUrl } = await discoverAccessControl(inboxUrl);
            await solidSession.fetch(inboxAclUrl, {
                method: 'PUT',
                headers: { 'Content-Type': 'text/turtle' },
                body: buildInboxAcl(inboxUrl, webId, []),
            });
        } catch (err) {
            console.warn('[pod] inbox ACL failed:', err);
        }
        // Advertise it from the profile.
        try {
            await solidSession.fetch(webId.split('#')[0], {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/sparql-update' },
                body: `INSERT DATA {\n  <${webId}> <${INBOX_PRED}> <${inboxUrl}> .\n}\n`,
            });
        } catch (err) {
            console.warn('[pod] inbox created but profile link failed:', err);
        }
        return inboxUrl;
    } catch (err) {
        console.warn('[pod] podEnsureInbox failed:', err);
        return null;
    }
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

/**
 * Grant a WebID read-only access to our inbox (R78 L2), so an always-on gateway that
 * is not publicly reachable can poll it for invitations and push us. Read-only and
 * inbox-scoped; owner control and public-Append are preserved. Idempotent.
 */
export async function podGrantInboxReader(readerWebId) {
    const me = solidSession?.info?.webId;
    if (!readerWebId || !me || !solidSession?.info?.isLoggedIn) return false;
    const inbox = await podEnsureInbox();
    if (!inbox) return false;
    try {
        const { url: inboxAclUrl } = await discoverAccessControl(inbox);
        const res = await solidSession.fetch(inboxAclUrl, {
            method: 'PUT',
            headers: { 'Content-Type': 'text/turtle' },
            body: buildInboxAcl(inbox, me, [readerWebId]),
        });
        return !!(res && res.ok);
    } catch (err) {
        console.warn('[pod] podGrantInboxReader failed:', err);
        return false;
    }
}

/** POST a chat invite to a recipient's inbox. Returns true on success. */
export async function podSendChatInvite(recipientWebId, { container, title = '' } = {}) {
    const me = solidSession?.info?.webId;
    if (!recipientWebId || !container || !me || !solidSession?.info?.isLoggedIn) return false;
    const inbox = await podDiscoverInbox(recipientWebId);
    if (!inbox) return false;
    try {
        const res = await solidSession.fetch(inbox, {
            method: 'POST',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify(buildInviteNotification({
                from: me, to: recipientWebId, container, title,
            })),
        });
        return !!(res && res.ok);
    } catch (err) {
        console.warn('[pod] podSendChatInvite failed:', err);
        return false;
    }
}

// The inbox is public-Append, so anyone may drop notifications in it. Bound the work
// per read so a flooded inbox cannot hang the client or hammer the pod with an
// unbounded number of sequential fetches (R80 A1). Pruning the inbox is the pod
// operator's lever; we just refuse to process an unbounded batch at once.
const MAX_INBOX_NOTIFICATIONS = 100;

/**
 * Read our inbox and return the pending chat invitations: [{ id, from, container,
 * title }]. Only notifications that reference a chat container are surfaced;
 * anything else in the inbox is ignored. Processes at most MAX_INBOX_NOTIFICATIONS
 * per call.
 */
export async function podReadInboxNotifications() {
    const inbox = await podDiscoverInbox(solidSession?.info?.webId);
    if (!inbox) return [];
    let urls = [];
    try {
        const res = await solidSession.fetch(inbox, { headers: { Accept: 'application/ld+json' } });
        if (!res || !res.ok) return [];
        urls = parseInboxListing(await res.json(), inbox);
    } catch (err) {
        console.warn('[pod] podReadInboxNotifications listing failed:', err);
        return [];
    }
    if (urls.length > MAX_INBOX_NOTIFICATIONS) urls = urls.slice(0, MAX_INBOX_NOTIFICATIONS);
    const out = [];
    for (const url of urls) {
        try {
            const r = await solidSession.fetch(url, { headers: { Accept: 'application/ld+json' } });
            if (!r || !r.ok) continue;
            const inv = parseInviteNotification(await r.json());
            if (inv && inv.container) out.push({ id: url, from: inv.from, container: inv.container, title: inv.title });
        } catch { /* skip a single unreadable notification */ }
    }
    return out;
}

/** Delete a processed notification from our inbox. */
export async function podDeleteInboxNotification(url) {
    if (!url || !solidSession?.info?.isLoggedIn) return false;
    try {
        const res = await solidSession.fetch(url, { method: 'DELETE' });
        return !!(res && (res.ok || res.status === 404));
    } catch (err) {
        console.warn('[pod] podDeleteInboxNotification failed:', err);
        return false;
    }
}

// ── Gateway-free DM delivery (R103) ──────────────────────────────────────────
//
// Without a gateway to relay ciphertext, a DM is delivered by dropping the
// ratchet-encrypted envelope into the recipient's pod. Each user exposes
// proxion/dm-inbox/ as public-Append (anyone may POST an envelope; only the
// owner can list, read, and delete). The envelope is opaque to pod.js: the
// caller builds it from e2e.js output (ciphertext, nonce, ratchet_pub, msg_num,
// pn, sender key + WebID). The pod stores only ciphertext.

const DM_INBOX_PATH = 'proxion/dm-inbox/';
const MAX_DM_DROPS = 200;

/** The recipient's DM drop-box URL, from their pod storage root. */
export function dmInboxUrlFor(podRoot) {
    if (!podRoot) return null;
    return podRoot.replace(/\/?$/, '/') + DM_INBOX_PATH;
}

/** Ensure OUR DM drop-box exists and is public-Append (owner read/write/control). */
export async function podEnsureDmInbox() {
    const root = podStorageRoot();
    const webId = solidSession?.info?.webId;
    if (!root || !webId || !solidSession?.info?.isLoggedIn) return null;
    const inboxUrl = root.replace(/\/?$/, '/') + DM_INBOX_PATH;
    try {
        const put = await solidSession.fetch(inboxUrl, {
            method: 'PUT',
            headers: {
                'Content-Type': 'text/turtle',
                Link: '<http://www.w3.org/ns/ldp#BasicContainer>; rel="type"',
            },
            body: '',
        });
        if (!put || !(put.ok || put.status === 409 || put.status === 405)) return null;
        try {
            const { url: aclUrl } = await discoverAccessControl(inboxUrl);
            await solidSession.fetch(aclUrl, {
                method: 'PUT',
                headers: { 'Content-Type': 'text/turtle' },
                body: buildInboxAcl(inboxUrl, webId, []),
            });
        } catch (err) {
            console.warn('[pod] DM inbox ACL failed:', err);
        }
        return inboxUrl;
    } catch (err) {
        console.warn('[pod] podEnsureDmInbox failed:', err);
        return null;
    }
}

/** Drop an encrypted DM envelope into a recipient's public-Append DM inbox. */
export async function podDropDm(recipientPodRoot, envelope) {
    const inbox = dmInboxUrlFor(recipientPodRoot);
    if (!inbox || !envelope || !solidSession?.info?.isLoggedIn) return false;
    try {
        const res = await solidSession.fetch(inbox, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(envelope),
        });
        return !!(res && res.ok);
    } catch (err) {
        console.warn('[pod] podDropDm failed:', err);
        return false;
    }
}

/** Read our own DM inbox: returns [{ url, envelope }] for each dropped envelope. */
export async function podReadDmDrops() {
    const root = podStorageRoot();
    if (!root || !solidSession?.info?.isLoggedIn) return [];
    const inbox = root.replace(/\/?$/, '/') + DM_INBOX_PATH;
    let urls = [];
    try {
        const res = await solidSession.fetch(inbox, { headers: { Accept: 'application/ld+json' } });
        if (!res || !res.ok) return [];
        urls = parseInboxListing(await res.json(), inbox);
    } catch (err) {
        console.warn('[pod] podReadDmDrops listing failed:', err);
        return [];
    }
    if (urls.length > MAX_DM_DROPS) urls = urls.slice(0, MAX_DM_DROPS);
    const out = [];
    for (const url of urls) {
        try {
            const r = await solidSession.fetch(url, { headers: { Accept: 'application/json' } });
            if (!r || !r.ok) continue;
            out.push({ url, envelope: await r.json() });
        } catch { /* skip one unreadable drop */ }
    }
    return out;
}

/** Delete a consumed DM envelope from our inbox. */
export async function podDeleteDmDrop(url) {
    if (!url || !solidSession?.info?.isLoggedIn) return false;
    try {
        const res = await solidSession.fetch(url, { method: 'DELETE' });
        return !!(res && (res.ok || res.status === 404));
    } catch (err) {
        console.warn('[pod] podDeleteDmDrop failed:', err);
        return false;
    }
}

// ── Presence (R104): a public-read heartbeat peers poll or subscribe to ───────
//
// Without a gateway to fan out presence, each user publishes a small public-read
// proxion/presence.json ({ status, heartbeat }) refreshed on a timer. Contacts
// derive online/away/offline from how fresh the heartbeat is (see webpresence.js).

const PRESENCE_PATH = 'proxion/presence.json';
let _presenceAclWritten = false;

/** The presence resource URL for a pod storage root. */
export function presenceUrlFor(podRoot) {
    if (!podRoot) return null;
    return podRoot.replace(/\/?$/, '/') + PRESENCE_PATH;
}

/** Publish our presence heartbeat. Writes a public-read ACL once. */
export async function podWritePresence(status) {
    const root = podStorageRoot();
    const webId = solidSession?.info?.webId;
    if (!root || !webId || !solidSession?.info?.isLoggedIn) return false;
    const url = root.replace(/\/?$/, '/') + PRESENCE_PATH;
    try {
        const res = await solidSession.fetch(url, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ v: 1, status, heartbeat: Date.now() }),
        });
        if (!res || !res.ok) return false;
        if (!_presenceAclWritten) {
            try {
                const { url: aclUrl } = await discoverAccessControl(url);
                await solidSession.fetch(aclUrl, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'text/turtle' },
                    body: `@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n@prefix foaf: <http://xmlns.com/foaf/0.1/>.\n`
                        + `<#owner> a acl:Authorization; acl:agent <${webId}>; acl:accessTo <${url}>; acl:mode acl:Read, acl:Write, acl:Control.\n`
                        + `<#public> a acl:Authorization; acl:agentClass foaf:Agent; acl:accessTo <${url}>; acl:mode acl:Read.\n`,
                });
                _presenceAclWritten = true;
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
export async function podReadPresence(peerPodRoot) {
    const url = presenceUrlFor(peerPodRoot);
    if (!url || !solidSession?.info?.isLoggedIn) return null;
    try {
        const res = await solidSession.fetch(url, { headers: { Accept: 'application/json' } });
        if (!res || !res.ok) return null;
        const d = await res.json();
        if (d && typeof d.status === 'string') return { status: d.status, heartbeat: Number(d.heartbeat) || 0 };
    } catch { /* peer has no presence / offline */ }
    return null;
}

// ── Solid social graph (contact import) ──────────────────────────────────────
//
// Reading who a Solid user knows so you can start a conversation with them. Uses
// the standard terms every Solid app writes, so it works on contacts made in
// SolidOS / POD-CHAT / any Solid app, not just Proxion.

const _FOAF_KNOWS = 'http://xmlns.com/foaf/0.1/knows';
const _FOAF_NAME = 'http://xmlns.com/foaf/0.1/name';
const _VCARD_FN = 'http://www.w3.org/2006/vcard/ns#fn';

function _jsonldNodes(json) {
    if (!json) return [];
    if (Array.isArray(json)) return json;
    if (Array.isArray(json['@graph'])) return json['@graph'];
    return [json];
}
function _idsOf(node, predicate) {
    const raw = node[predicate];
    if (!raw) return [];
    const arr = Array.isArray(raw) ? raw : [raw];
    return arr
        .map(v => (v && typeof v === 'object') ? v['@id'] : (typeof v === 'string' ? v : null))
        .filter(id => id && /^https?:\/\//.test(id));
}

/**
 * The WebIDs a Solid user lists as `foaf:knows` in their profile. Defaults to
 * our own profile. These are people you can invite to a conversation.
 */
export async function podReadKnownWebIds(profileUrl) {
    const url = profileUrl || (solidSession?.info?.webId);
    if (!url || !solidSession?.info?.isLoggedIn) return [];
    try {
        const res = await solidSession.fetch(url, { headers: { Accept: 'application/ld+json' } });
        if (!res || !res.ok) return [];
        const json = await res.json();
        const out = new Set();
        for (const node of _jsonldNodes(json)) {
            for (const id of _idsOf(node, _FOAF_KNOWS)) out.add(id);
        }
        return [...out];
    } catch (err) {
        console.warn('[pod] podReadKnownWebIds failed:', err);
        return [];
    }
}

/** A human name for a WebID, from `foaf:name` or `vcard:fn`, or '' if none. */
export async function podResolveWebIdName(webid) {
    if (!webid || !solidSession?.info?.isLoggedIn) return '';
    try {
        const res = await solidSession.fetch(webid, { headers: { Accept: 'application/ld+json' } });
        if (!res || !res.ok) return '';
        const json = await res.json();
        for (const node of _jsonldNodes(json)) {
            for (const p of [_FOAF_NAME, _VCARD_FN]) {
                const raw = node[p];
                if (!raw) continue;
                const v = Array.isArray(raw) ? raw[0] : raw;
                const name = (v && typeof v === 'object' && '@value' in v) ? v['@value']
                    : (typeof v === 'string' ? v : '');
                if (name) return String(name);
            }
        }
        return '';
    } catch (err) {
        console.warn('[pod] podResolveWebIdName failed:', err);
        return '';
    }
}

/** Known WebIDs paired with a resolved display name (name may be ''). */
export async function podImportContacts(profileUrl) {
    const webids = await podReadKnownWebIds(profileUrl);
    const named = await Promise.all(webids.map(async (webid) => ({
        webid, name: await podResolveWebIdName(webid),
    })));
    return named;
}

// Namespaces used by the Solid chat ecosystem (SolidOS Long Chat, POD-CHAT).
export const LONGCHAT_CONTEXT = Object.freeze({
    sioc: 'http://rdfs.org/sioc/ns#',
    dct: 'http://purl.org/dc/terms/',
    foaf: 'http://xmlns.com/foaf/0.1/',
});
const XSD_DATETIME = 'http://www.w3.org/2001/XMLSchema#dateTime';

/**
 * Add the standard Solid chat vocabulary to a room message, alongside the px:
 * terms, so other Solid apps (SolidOS databrowser, POD-CHAT) can read it.
 *
 * ROOMS ONLY, on purpose. Rooms are shared by design, so their history is
 * plaintext and interoperable. Direct messages are end-to-end encrypted, and you
 * cannot have bytes that a third-party app can read AND that no third party can
 * read, so DMs stay px:-only and deliberately non-interoperable.
 *
 * Scope note: only the three mandatory per-message terms are emitted here on the
 * initial write. Edits and deletes are mapped separately (PLAN_ROUND_68 Phase B):
 * an edit rewrites sioc:content in place via podEditChatMessageAt, a delete
 * appends a schema:dateDeleted tombstone via podSoftDeleteChatMessageAt.
 * Replies/threads (sioc:has_reply, sioc:Thread) are still px:-only: Long Chat
 * models replies on the parent and Proxion on the child, so that mapping has to
 * be prototyped against a real SolidOS thread rather than guessed (Phase C).
 */
export function applyLongChatTerms(doc, msg, timestamp) {
    Object.assign(doc['@context'], LONGCHAT_CONTEXT);
    doc['sioc:content'] = msg.content || '';
    doc['dct:created'] = { '@value': timestamp, '@type': XSD_DATETIME };
    // foaf:maker must be an IRI, not a literal. A pod-connected user's WebID is
    // an http(s) IRI; a pod-less identity is a did:key, which is a valid IRI but
    // is not dereferenceable, so other apps will show the id rather than a name.
    // That is the expected limit of interop for users without a pod.
    const maker = msg.from_webid || '';
    if (maker) doc['foaf:maker'] = { '@id': maker };
    return doc;
}

export async function podDeleteMessage(threadId, messageId, isRoom = true) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const dir = isRoom ? `rooms/${threadId}` : `dm/${threadId}`;
    try {
        await solidSession.fetch(
            `${root}proxion/${dir}/messages/${messageId}.jsonld`,
            { method: 'DELETE' }
        );
    } catch (err) {
        console.warn('[pod] podDeleteMessage failed:', err);
    }
}

// --- Room Members ---

export async function podWriteRoomMembers(roomId, members) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        await solidSession.fetch(`${root}proxion/rooms/${roomId}/members.jsonld`, {
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

// --- Reactions ---

export async function podWriteReactions(roomId, messageId, reactions) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        await solidSession.fetch(
            `${root}proxion/rooms/${roomId}/reactions/${messageId}.jsonld`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': 'px:ReactionSet',
                'px:messageId': messageId,
                'px:reactions': reactions,
                'px:updatedAt': new Date().toISOString(),
            }),
        });
    } catch (err) {
        console.warn('[pod] podWriteReactions failed:', err);
    }
}

/**
 * R101: mirror one reaction into the Long Chat day file as a schema:LikeAction
 * targeting the message, so other Solid apps see reactions (not just our px:
 * ReactionSet). Add inserts the action, un-react deletes the same triples. Needs
 * the message's timestamp (threaded from the client) to build its date-partitioned
 * IRI; a no-op without it. Best-effort, additive.
 */
export async function podWriteReactionAction(roomId, messageId, messageTimestamp, emoji, reactorWebId, add) {
    const root = podStorageRoot();
    if (!root || !messageTimestamp || !solidSession?.info?.isLoggedIn) return false;
    const container = chatRootUrl(root, roomId);
    const msgIri = messageIriAt(container, messageId, messageTimestamp);
    const dayFile = dayFileAt(container, messageTimestamp);
    const actionIri = `${dayFile}#react-${encodeURIComponent(messageId)}`
        + `-${encodeURIComponent(reactorWebId || 'anon')}-${encodeURIComponent(emoji)}`;
    const triples = reactionActionTriples({ actionIri, msgIri, agentIri: reactorWebId, emoji });
    try {
        const r = await podRdfPatch(dayFile, add ? { inserts: triples } : { deletes: triples });
        return !!(r && r.ok);
    } catch (err) {
        console.warn('[pod] podWriteReactionAction failed:', err);
        return false;
    }
}

// --- Read State ---

export async function podWriteReadState(threadId, lastMessageId) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        await solidSession.fetch(`${root}proxion/readstate/${threadId}.jsonld`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': 'px:ReadState',
                'px:threadId': threadId,
                'px:lastReadMessageId': lastMessageId,
                'px:updatedAt': new Date().toISOString(),
            }),
        });
    } catch (err) {
        console.warn('[pod] podWriteReadState failed:', err);
    }
}

export async function podReadReadState(threadId) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return null;
    try {
        const res = await solidSession.fetch(
            `${root}proxion/readstate/${threadId}.jsonld`,
            { headers: { Accept: 'application/ld+json' } }
        );
        if (!res.ok) return null;
        return res.json();
    } catch {
        return null;
    }
}

// --- Voice Audio ---

export async function podUploadVoiceAudio(roomId, messageId, audioBlob) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return null;
    const fileUri = `${root}proxion/rooms/${roomId}/files/${messageId}.webm`;
    try {
        await solidSession.fetch(fileUri, {
            method: 'PUT',
            headers: { 'Content-Type': 'audio/webm' },
            body: audioBlob,
        });
        return fileUri;
    } catch (err) {
        console.warn('[pod] podUploadVoiceAudio failed:', err);
        return null;
    }
}

export async function podDeleteVoiceAudio(roomId, messageId) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        await solidSession.fetch(
            `${root}proxion/rooms/${roomId}/files/${messageId}.webm`,
            { method: 'DELETE' }
        );
    } catch (err) {
        console.warn('[pod] podDeleteVoiceAudio failed:', err);
    }
}

// --- File Uploads ---

export async function podUploadFile(roomId, messageId, filename, fileBlob) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return null;
    const safeName = filename.replace(/[^a-zA-Z0-9._-]/g, '_');
    const fileUri = `${root}proxion/rooms/${roomId}/files/${messageId}/${safeName}`;
    try {
        await solidSession.fetch(fileUri, {
            method: 'PUT',
            headers: { 'Content-Type': fileBlob.type || 'application/octet-stream' },
            body: fileBlob,
        });
        return fileUri;
    } catch (err) {
        console.warn('[pod] podUploadFile failed:', err);
        return null;
    }
}

// --- Scheduled Messages ---

export async function podWriteScheduled(id, threadId, sendAt, contentPreview) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        await solidSession.fetch(`${root}proxion/scheduled/${id}.jsonld`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': 'px:ScheduledMessage',
                'px:id': id,
                'px:threadId': threadId,
                'px:sendAt': sendAt,
                'px:contentPreview': contentPreview,
                'px:createdAt': new Date().toISOString(),
            }),
        });
    } catch (err) {
        console.warn('[pod] podWriteScheduled failed:', err);
    }
}

export async function podDeleteScheduled(id) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        await solidSession.fetch(`${root}proxion/scheduled/${id}.jsonld`, { method: 'DELETE' });
    } catch (err) {
        console.warn('[pod] podDeleteScheduled failed:', err);
    }
}

// --- Webhooks ---

async function _sha256Hex(str) {
    const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
    return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
}

export async function podWriteWebhook(id, wh) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        const tokenHash = wh.token ? await _sha256Hex(wh.token) : '';
        await solidSession.fetch(`${root}proxion/webhooks/${id}.jsonld`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': 'px:Webhook',
                'px:id': id,
                'px:direction': wh.direction,
                'px:botName': wh.bot_name,
                'px:url': wh.url || null,
                'px:tokenHash': tokenHash,
                'px:createdAt': new Date().toISOString(),
            }),
        });
    } catch (err) {
        console.warn('[pod] podWriteWebhook failed:', err);
    }
}

export async function podDeleteWebhook(id) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        await solidSession.fetch(`${root}proxion/webhooks/${id}.jsonld`, { method: 'DELETE' });
    } catch (err) {
        console.warn('[pod] podDeleteWebhook failed:', err);
    }
}

// --- Shared px: document writer (R62) ---

// PUT a px: JSON-LD document. Centralizes the envelope every px: writer repeats
// ({@context, @type, ...props, px:updatedAt}). `path` is relative to the pod
// root. Owner-only by construction: resources under proxion/ inherit the
// container's owner-only ACL; nothing here grants member access.
async function _writePxDoc(path, type, props) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    try {
        await solidSession.fetch(root + path, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': type,
                ...props,
                'px:updatedAt': new Date().toISOString(),
            }),
        });
    } catch (err) {
        console.warn('[pod] _writePxDoc failed:', path, err);
    }
}

// --- Generic index helpers ---

async function _readIndex(indexUrl) {
    try {
        const res = await solidSession.fetch(indexUrl, { headers: { Accept: 'application/ld+json' } });
        if (!res.ok) return [];
        const raw = await res.json();
        const ids = raw?.['px:ids'] ?? raw?.ids ?? [];
        return Array.isArray(ids) ? ids.filter(id => typeof id === 'string' && SAFE_ID_RE.test(id)) : [];
    } catch { return []; }
}

async function _writeIndex(indexUrl, ids) {
    try {
        await solidSession.fetch(indexUrl, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': 'px:Index',
                'px:ids': ids,
                'px:updatedAt': new Date().toISOString(),
            }),
        });
    } catch (err) {
        console.warn('[pod] _writeIndex failed:', indexUrl, err);
    }
}

async function _addToIndex(indexUrl, id) {
    const ids = await _readIndex(indexUrl);
    if (!ids.includes(id)) {
        ids.push(id);
        await _writeIndex(indexUrl, ids);
    }
}

async function _removeFromIndex(indexUrl, id) {
    const ids = await _readIndex(indexUrl);
    const filtered = ids.filter(i => i !== id);
    if (filtered.length !== ids.length) await _writeIndex(indexUrl, filtered);
}

// --- Contacts ---

export async function podWriteContact(certId, certObj) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn || !SAFE_ID_RE.test(certId)) return;
    try {
        await solidSession.fetch(`${root}proxion/contacts/${certId}.jsonld`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': 'px:Contact',
                '@id': `${root}proxion/contacts/${certId}.jsonld`,
                'px:certId': certId,
                'px:certificate': certObj,
                'px:updatedAt': new Date().toISOString(),
            }),
        });
        await _addToIndex(`${root}proxion/contacts/index.jsonld`, certId);
    } catch (err) {
        console.warn('[pod] podWriteContact failed:', err);
    }
}

export async function podReadContacts() {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return [];
    const ids = await _readIndex(`${root}proxion/contacts/index.jsonld`);
    if (!ids.length) return [];
    const results = await Promise.allSettled(
        ids.map(id => solidSession.fetch(`${root}proxion/contacts/${id}.jsonld`)
            .then(r => r.ok ? r.json() : null)
            .then(doc => doc?.['px:certificate'] ?? null)
            .catch(() => null))
    );
    return results
        .filter(r => r.status === 'fulfilled' && r.value)
        .map(r => r.value);
}

export async function podDeleteContact(certId) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn || !SAFE_ID_RE.test(certId)) return;
    try {
        await solidSession.fetch(`${root}proxion/contacts/${certId}.jsonld`, { method: 'DELETE' });
        await _removeFromIndex(`${root}proxion/contacts/index.jsonld`, certId);
    } catch (err) {
        console.warn('[pod] podDeleteContact failed:', err);
    }
}

// --- Invites ---

export async function podWriteInvite(invitationId, inviteObj) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const safeId = invitationId.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 128);
    try {
        await solidSession.fetch(`${root}proxion/invites/${safeId}.jsonld`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/ld+json' },
            body: JSON.stringify({
                '@context': { px: 'https://proxion.dev/vocab/v1#' },
                '@type': 'px:PendingInvite',
                'px:invitationId': invitationId,
                'px:invite': inviteObj,
                'px:receivedAt': new Date().toISOString(),
            }),
        });
        await _addToIndex(`${root}proxion/invites/index.jsonld`, safeId);
    } catch (err) {
        console.warn('[pod] podWriteInvite failed:', err);
    }
}

export async function podReadInvites() {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return [];
    const ids = await _readIndex(`${root}proxion/invites/index.jsonld`);
    if (!ids.length) return [];
    const results = await Promise.allSettled(
        ids.map(id => solidSession.fetch(`${root}proxion/invites/${id}.jsonld`)
            .then(r => r.ok ? r.json() : null)
            .then(doc => doc?.['px:invite'] ?? null)
            .catch(() => null))
    );
    return results
        .filter(r => r.status === 'fulfilled' && r.value)
        .map(r => r.value);
}

export async function podDeleteInvite(invitationId) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const safeId = invitationId.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 128);
    try {
        await solidSession.fetch(`${root}proxion/invites/${safeId}.jsonld`, { method: 'DELETE' });
        await _removeFromIndex(`${root}proxion/invites/index.jsonld`, safeId);
    } catch (err) {
        console.warn('[pod] podDeleteInvite failed:', err);
    }
}

// --- Room index ---

export async function podWriteRoomIndex(roomIds) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    await _writeIndex(`${root}proxion/rooms/index.jsonld`, roomIds.filter(id => SAFE_ID_RE.test(id)));
}

export async function podReadRoomIndex() {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return [];
    return _readIndex(`${root}proxion/rooms/index.jsonld`);
}

export async function _podUpdateRoomIndex(roomId, add = true) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn || !SAFE_ID_RE.test(roomId)) return;
    const indexUrl = `${root}proxion/rooms/index.jsonld`;
    if (add) await _addToIndex(indexUrl, roomId);
    else await _removeFromIndex(indexUrl, roomId);
}

// --- DM thread index ---

export async function podWriteDmIndex(threadIds) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    await _writeIndex(`${root}proxion/dm/index.jsonld`, threadIds);
}

export async function podReadDmIndex() {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return [];
    return _readIndex(`${root}proxion/dm/index.jsonld`);
}

export async function _podUpdateDmIndex(threadId, add = true) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const indexUrl = `${root}proxion/dm/index.jsonld`;
    if (add) await _addToIndex(indexUrl, threadId);
    else await _removeFromIndex(indexUrl, threadId);
}

// --- Opt-in DM archive (R61) ---
//
// Off by default. When enabled AND a pod is connected, decrypted DM history is
// written to your own pod as open px:Message JSON-LD, so it syncs across your
// devices and any Solid app you authorize can read it. It stays owner-only: DM
// resources inherit the proxion/ container's owner-only ACL, and we never grant
// member read here (the other party keeps their own copy on their own pod).

export function dmPodArchiveEnabled() {
    try {
        return typeof localStorage !== 'undefined' &&
            localStorage.getItem('proxion_dm_pod_archive') === '1';
    } catch {
        return false;
    }
}

// Write a single DM message to the pod archive (no-op unless enabled + logged
// in). Reuses the canonical JSON-LD writer and maintains a per-thread message
// index so read-back can enumerate without a container LIST.
export async function podArchiveDmMessage(threadId, msg) {
    if (!dmPodArchiveEnabled()) return;
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    if (!SAFE_ID_RE.test(threadId) || !SAFE_ID_RE.test(msg?.message_id || '')) return;
    await podWriteMessageJsonLd(threadId, msg.message_id, msg, false);
    await _addToIndex(`${root}proxion/dm/${threadId}/messages/index.jsonld`, msg.message_id);
    await _podUpdateDmIndex(threadId, true);
}

// Remove a DM message from the pod archive (best-effort; only when logged in).
export async function podArchiveDeleteDmMessage(threadId, messageId) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    if (!SAFE_ID_RE.test(threadId) || !SAFE_ID_RE.test(messageId)) return;
    await podDeleteMessage(threadId, messageId, false);
    await _removeFromIndex(`${root}proxion/dm/${threadId}/messages/index.jsonld`, messageId);
}

// Read archived DM history for a thread back into the message shape the client
// renders. Reading your own archive is always fine (independent of the write
// toggle), so enabling archiving on one device restores history on another.
export async function podReadDmMessages(threadId) {
    if (!SAFE_ID_RE.test(threadId)) return [];
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return [];
    const base = `${root}proxion/dm/${threadId}/messages/`;
    const ids = (await _readIndex(`${base}index.jsonld`)).slice(-200);
    if (!ids.length) return [];
    const results = await Promise.allSettled(
        ids.map((id) => solidSession.fetch(`${base}${id}.jsonld`)
            .then((r) => (r.ok ? r.text() : null))
            .then((text) => {
                if (!text || text.length > 65536) return null;
                const doc = JSON.parse(text);
                if (doc?.['@type'] !== 'px:Message') return null;
                const mid = doc['px:messageId'];
                if (typeof mid !== 'string' || !SAFE_ID_RE.test(mid)) return null;
                return {
                    message_id: mid,
                    thread_id: threadId,
                    content: doc['px:content'] || '',
                    content_type: doc['px:contentType'] || 'text',
                    from_webid: doc['px:fromWebid'] || '',
                    from_display_name: doc['px:fromName'] || '',
                    timestamp: doc['px:timestamp'] || '',
                    reply_to_id: doc['px:replyToId'] || null,
                };
            })
            .catch(() => null))
    );
    const msgs = results
        .filter((r) => r.status === 'fulfilled' && r.value)
        .map((r) => r.value);
    msgs.sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''));
    return msgs;
}

// --- Opt-in bookmarks + settings sync (R62) ---
//
// Off by default. One toggle governs both. When on AND a pod is connected,
// saved messages and account settings mirror to your own pod (owner-only, open
// px: RDF) so they sync across your devices. Kept separate from the R61 DM
// archive toggle. Bookmarks can quote DMs, so they are treated as private.

export function podSyncEnabled() {
    try {
        return typeof localStorage !== 'undefined' &&
            localStorage.getItem('proxion_pod_sync') === '1';
    } catch {
        return false;
    }
}

// -- Saved messages (bookmarks) --

export async function podSyncSavedMessage(item) {
    if (!podSyncEnabled()) return;
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const id = item?.id;
    if (typeof id !== 'string' || !SAFE_ID_RE.test(id)) return;
    await _writePxDoc(`proxion/saved/${id}.jsonld`, 'px:SavedMessage', {
        'px:messageId': id,
        'px:threadId': item.thread_id || '',
        'px:threadType': item.thread_type || '',
        'px:threadLabel': item.thread_label || '',
        'px:fromName': item.from_name || '',
        'px:content': item.content || '',
        'px:hasFile': !!item.has_file,
        'px:fileKind': item.file_kind || '',
        'px:timestamp': item.timestamp || '',
        'px:savedAt': item.addedAt || Date.now(),
    });
    await _addToIndex(`${root}proxion/saved/index.jsonld`, id);
}

export async function podSyncRemoveSavedMessage(id) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    if (typeof id !== 'string' || !SAFE_ID_RE.test(id)) return;
    try {
        await solidSession.fetch(`${root}proxion/saved/${id}.jsonld`, { method: 'DELETE' });
    } catch (err) {
        console.warn('[pod] podSyncRemoveSavedMessage failed:', err);
    }
    await _removeFromIndex(`${root}proxion/saved/index.jsonld`, id);
}

export async function podReadSavedMessages() {
    if (!podSyncEnabled()) return [];
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return [];
    const base = `${root}proxion/saved/`;
    const ids = (await _readIndex(`${base}index.jsonld`)).slice(-500);
    if (!ids.length) return [];
    const results = await Promise.allSettled(
        ids.map((id) => solidSession.fetch(`${base}${id}.jsonld`)
            .then((r) => (r.ok ? r.text() : null))
            .then((text) => {
                if (!text || text.length > 65536) return null;
                const doc = JSON.parse(text);
                if (doc?.['@type'] !== 'px:SavedMessage') return null;
                const mid = doc['px:messageId'];
                if (typeof mid !== 'string' || !SAFE_ID_RE.test(mid)) return null;
                return {
                    id: mid,
                    thread_id: doc['px:threadId'] || '',
                    thread_type: doc['px:threadType'] || '',
                    thread_label: doc['px:threadLabel'] || '',
                    from_name: doc['px:fromName'] || '',
                    content: doc['px:content'] || '',
                    has_file: !!doc['px:hasFile'],
                    file_kind: doc['px:fileKind'] || '',
                    timestamp: doc['px:timestamp'] || '',
                    addedAt: doc['px:savedAt'] || Date.now(),
                };
            })
            .catch(() => null))
    );
    return results.filter((r) => r.status === 'fulfilled' && r.value).map((r) => r.value);
}

// -- Account settings --

export async function podWriteSettings(prefs) {
    if (!podSyncEnabled()) return;
    if (!solidSession.info.isLoggedIn) return;
    await _writePxDoc('proxion/settings.jsonld', 'px:Settings', {
        'px:prefs': prefs && typeof prefs === 'object' ? prefs : {},
    });
}

export async function podReadSettings() {
    if (!podSyncEnabled()) return null;
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return null;
    try {
        const res = await solidSession.fetch(`${root}proxion/settings.jsonld`,
            { headers: { Accept: 'application/ld+json' } });
        if (!res.ok) return null;
        const doc = await res.json();
        const prefs = doc?.['px:prefs'];
        return prefs && typeof prefs === 'object' ? prefs : null;
    } catch {
        return null;
    }
}

// -- GIF tray favorites (R63) --
//
// Under the same opt-in sync toggle. Each favorite is stored as a REAL image
// resource at a clean URL (so other Solid apps see an image, not base64 in
// JSON) plus a px:GifFavorite metadata doc referencing it, and an index.

const _GIF_MIMES = new Set(['image/gif', 'image/png', 'image/webp', 'image/jpeg', 'image/avif']);

function _b64ToBytes(b64) {
    const bin = atob(b64 || '');
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
}

function _bytesToB64(buf) {
    let bin = '';
    for (let i = 0; i < buf.length; i++) bin += String.fromCharCode(buf[i]);
    return btoa(bin);
}

export async function podSyncGifFavorite(fav) {
    if (!podSyncEnabled()) return;
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    const id = fav?.id;
    if (typeof id !== 'string' || !SAFE_ID_RE.test(id)) return;
    const mime = (fav.mime || '').toLowerCase();
    if (!_GIF_MIMES.has(mime) || !fav.data_b64) return;
    const imageUrl = `${root}proxion/gifs/${id}`;
    try {
        await solidSession.fetch(imageUrl, {
            method: 'PUT', headers: { 'Content-Type': mime }, body: _b64ToBytes(fav.data_b64),
        });
    } catch (err) {
        console.warn('[pod] gif image PUT failed:', err);
        return;
    }
    await _writePxDoc(`proxion/gifs/${id}.jsonld`, 'px:GifFavorite', {
        'px:gifId': id,
        'px:filename': fav.filename || 'image',
        'px:mime': mime,
        'px:image': imageUrl,
        'px:addedAt': fav.addedAt || Date.now(),
    });
    await _addToIndex(`${root}proxion/gifs/index.jsonld`, id);
}

export async function podSyncRemoveGifFavorite(id) {
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return;
    if (typeof id !== 'string' || !SAFE_ID_RE.test(id)) return;
    for (const url of [`${root}proxion/gifs/${id}`, `${root}proxion/gifs/${id}.jsonld`]) {
        try { await solidSession.fetch(url, { method: 'DELETE' }); } catch (_) { /* best-effort */ }
    }
    await _removeFromIndex(`${root}proxion/gifs/index.jsonld`, id);
}

export async function podReadGifFavorites() {
    if (!podSyncEnabled()) return [];
    const root = podStorageRoot();
    if (!root || !solidSession.info.isLoggedIn) return [];
    const base = `${root}proxion/gifs/`;
    const ids = (await _readIndex(`${base}index.jsonld`)).slice(-200);
    if (!ids.length) return [];
    const results = await Promise.allSettled(ids.map(async (id) => {
        const metaRes = await solidSession.fetch(`${base}${id}.jsonld`);
        if (!metaRes.ok) return null;
        const metaText = await metaRes.text();
        if (metaText.length > 16384) return null;
        const doc = JSON.parse(metaText);
        if (doc?.['@type'] !== 'px:GifFavorite') return null;
        const mime = (doc['px:mime'] || '').toLowerCase();
        if (!_GIF_MIMES.has(mime)) return null;
        const imgRes = await solidSession.fetch(`${base}${id}`);
        if (!imgRes.ok) return null;
        const buf = new Uint8Array(await imgRes.arrayBuffer());
        if (!buf.length || buf.length > 5 * 1024 * 1024) return null;
        return {
            id, filename: doc['px:filename'] || 'image', mime,
            data_b64: _bytesToB64(buf),
            addedAt: doc['px:addedAt'] || Date.now(), lastUsedAt: 0, useCount: 0,
        };
    }));
    return results.filter((r) => r.status === 'fulfilled' && r.value).map((r) => r.value);
}

// -- Mutes + blocks (R64) --
//
// Personal cross-device state under the same opt-in sync toggle. Small string
// lists; owner-only.

function _readPxList(url, listKey) {
    return solidSession.fetch(url, { headers: { Accept: 'application/ld+json' } })
        .then((res) => (res.ok ? res.json() : null))
        .then((doc) => {
            const arr = doc?.[listKey];
            return Array.isArray(arr) ? arr.filter((x) => typeof x === 'string') : null;
        })
        .catch(() => null);
}

export async function podWriteMutes(threadIds) {
    if (!podSyncEnabled() || !solidSession.info.isLoggedIn) return;
    await _writePxDoc('proxion/mutes.jsonld', 'px:MuteList', {
        'px:threads': Array.isArray(threadIds) ? threadIds.filter((x) => typeof x === 'string') : [],
    });
}

export async function podReadMutes() {
    if (!podSyncEnabled() || !solidSession.info.isLoggedIn) return null;
    const root = podStorageRoot();
    if (!root) return null;
    return _readPxList(`${root}proxion/mutes.jsonld`, 'px:threads');
}

export async function podWriteBlocks(webids) {
    if (!podSyncEnabled() || !solidSession.info.isLoggedIn) return;
    await _writePxDoc('proxion/blocks.jsonld', 'px:BlockList', {
        'px:webids': Array.isArray(webids) ? webids.filter((x) => typeof x === 'string') : [],
    });
}

export async function podReadBlocks() {
    if (!podSyncEnabled() || !solidSession.info.isLoggedIn) return null;
    const root = podStorageRoot();
    if (!root) return null;
    return _readPxList(`${root}proxion/blocks.jsonld`, 'px:webids');
}
