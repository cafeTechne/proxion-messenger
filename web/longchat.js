// longchat.js — interoperability with the Solid chat ecosystem (SolidOS Long
// Chat, POD-CHAT). PLAN_ROUND_67 phases B and C.
//
// Layout (verified against https://solid.github.io/chat/, not guessed):
//
//   proxion/rooms/{roomId}/index.ttl              <#this> a meeting:LongChat
//   proxion/rooms/{roomId}/YYYY/MM/DD/chat.ttl    that day's messages
//
// A day file links each message to the channel and then describes it:
//
//   <../../../index.ttl#this> meeting:message :msg1 .
//   :msg1 dct:created "..."^^xsd:dateTime ;
//         sioc:content "Hello world!" ;
//         foaf:maker <...webid...> .
//
// Two details that are easy to get wrong and would silently break interop:
//   * the linking predicate is meeting:message, NOT wf:message. The spec
//     declares a wf: prefix for other purposes, so inferring wf:message from
//     the namespace table gives you a document nobody reads.
//   * the channel title uses Dublin Core ELEMENTS (dc:) while message
//     timestamps use Dublin Core TERMS (dct:). Different namespaces.
//
// We WRITE Turtle (matching the ecosystem convention) and READ via JSON-LD
// content negotiation, so no RDF parser has to ship to the browser.

import {
    verifyLongChatProof, verifyLongChatDeleteProof,
    verifyLongChatReactionProof, verifyLongChatCancelProof,
} from './dmsig.js';

export const NS = Object.freeze({
    meeting: 'http://www.w3.org/ns/pim/meeting#',
    wf: 'http://www.w3.org/2005/01/wf/flow#',
    sioc: 'http://rdfs.org/sioc/ns#',
    dct: 'http://purl.org/dc/terms/',
    dc: 'http://purl.org/dc/elements/1.1/',
    foaf: 'http://xmlns.com/foaf/0.1/',
    xsd: 'http://www.w3.org/2001/XMLSchema#',
    schema: 'http://schema.org/',
    px: 'https://proxion.dev/vocab/v1#',
    // #4: the message signature literal required by the Solid chat SHACL shape.
    sec: 'https://w3id.org/security#',
});

export const P = Object.freeze({
    // Two predicates link a message to its channel, and they must BOTH be emitted:
    //   * the written spec (solid.github.io/chat) uses meeting:message,
    //   * the actual SolidOS databrowser enumerates messages with wf:message
    //     (verified against the mashlib bundle: kb.each(channel, ns.wf('message'))).
    // A chat carrying only one is invisible to half the ecosystem. POD-CHAT and
    // spec-followers read meeting:message; the reference app reads wf:message.
    message: NS.meeting + 'message',
    wfMessage: NS.wf + 'message',
    content: NS.sioc + 'content',
    created: NS.dct + 'created',
    maker: NS.foaf + 'maker',
    title: NS.dc + 'title',
    dateTime: NS.xsd + 'dateTime',
    // Phase B: the two SAFE edit/delete terms. A soft-delete is a schema.org
    // dateDeleted tombstone (the message node stays; readers hide its content).
    dateDeleted: NS.schema + 'dateDeleted',
    // R101.1: standard reply threading, so other Solid apps (SolidOS, etc.) show a
    // reply as a reply. Emitted parent-to-reply alongside our px: reply context.
    hasReply: NS.sioc + 'has_reply',
    // R101 reactions: a reaction as a schema.org social action targeting the
    // message, so other Solid apps see reactions (not just our px: ReactionSet).
    likeAction: NS.schema + 'LikeAction',
    agent: NS.schema + 'agent',
    target: NS.schema + 'target',
    // D4: a per-message monotonic order hint (px:, ours only) so a user's devices
    // agree on order despite client clock skew. Not part of the shared vocabulary.
    seq: NS.px + 'seq',
    // #4: the Solid chat SHACL shape's cryptographic signature over a message's
    // core fields (id, created, content, maker, px:fromName). One literal per
    // message, so it packs the signer did:key alongside the base64 signature (see
    // dmsig.js).
    proofValue: NS.sec + 'proofValue',
    // The author's DISPLAY name. Now emitted into the shared day file (not only the
    // px: archive) AND covered by the message signature, so a validly-signed
    // message cannot carry a name spoofing another author. Read back as
    // from_display_name.
    fromName: NS.px + 'fromName',
    // #5 Part A: proof that a schema:dateDeleted tombstone was authorized. A
    // "<did:key>|<b64sig>" over { message IRI, deletedIso }; the reader honours the
    // tombstone only when this verifies for the maker (self-delete) or room owner.
    deleteProof: NS.px + 'deleteProof',
    // #5 Part C: proof a reaction was made by its claimed agent, and proof an
    // un-react was made by that same agent. Both "<did:key>|<b64sig>" strings on
    // the LikeAction node (over the reaction fields / over { action IRI,
    // canceledIso } respectively).
    reactProof: NS.px + 'reactProof',
    cancelProof: NS.px + 'cancelProof',
});

// Characters that must never survive into a Turtle literal or IRI. Built from
// char codes so the source file itself stays free of raw control bytes.
const CONTROL_CHARS = new RegExp(`[${String.fromCharCode(0)}-${String.fromCharCode(31)}${String.fromCharCode(127)}]`, 'g');
const IRI_UNSAFE = /[<>"{}|\\^`\s]/g;

/**
 * Escape a string for use inside a Turtle/SPARQL double-quoted literal.
 *
 * This is a security boundary, not cosmetics. Message text is attacker-supplied
 * and ends up in a document on the user's pod. Without escaping, a message
 * containing a quote could terminate the literal and append arbitrary triples
 * (the RDF equivalent of SQL injection), letting a peer write statements into
 * someone else's pod under their authority.
 */
export function escapeTurtleLiteral(value) {
    return String(value == null ? '' : value)
        .replace(/\\/g, '\\\\')
        .replace(/"/g, '\\"')
        .replace(/\n/g, '\\n')
        .replace(/\r/g, '\\r')
        .replace(/\t/g, '\\t')
        // Newline, carriage return and tab became two-character escapes above.
        // Anything still in the control range is illegal raw in a literal.
        .replace(CONTROL_CHARS, '');
}

/**
 * Wrap an IRI for a Turtle/SPARQL document. Characters that would terminate the
 * IRI or inject syntax are stripped, so a hostile WebID cannot break out.
 */
export function iriRef(value) {
    const cleaned = String(value == null ? '' : value)
        .replace(CONTROL_CHARS, '')
        .replace(IRI_UNSAFE, '');
    return `<${cleaned}>`;
}

/**
 * UTC date partition for a message. The spec is explicit that "the URI must be
 * created from the UTC date of the message", so local time must not leak in.
 */
export function dayPath(date) {
    const parsed = date instanceof Date ? date : new Date(date || Date.now());
    const when = isNaN(parsed.getTime()) ? new Date() : parsed;
    const yyyy = String(when.getUTCFullYear()).padStart(4, '0');
    const mm = String(when.getUTCMonth() + 1).padStart(2, '0');
    const dd = String(when.getUTCDate()).padStart(2, '0');
    return `${yyyy}/${mm}/${dd}`;
}

// ── Chat addressing ──────────────────────────────────────────────────────────
//
// A chat is addressed two ways. A chat WE host lives under our own
// proxion/rooms/{roomId}/, so the roomId helpers below build that path. A SHARED
// chat can live anywhere (in a friend's pod, or one written by SolidOS at an
// arbitrary URL), so the *At helpers take the chat's container URL directly. The
// roomId helpers are thin wrappers over the container ones; nothing assumes a
// shared chat follows our own path convention.

export function chatRootUrl(storageRoot, roomId) {
    return `${storageRoot}proxion/rooms/${encodeURIComponent(roomId)}/`;
}

// The inverse of chatRootUrl: the roomId from a chat container URL, or null. Used
// to map type-index registrations back to rooms (B2 rehydration).
export function roomIdFromChatContainer(url) {
    const m = String(url || '').match(/\/proxion\/rooms\/([^/]+)\/?$/);
    if (!m) return null;
    try { return decodeURIComponent(m[1]); } catch { return m[1]; }
}

// Container-addressed (works for any chat, including ones in someone else's pod).
export function indexUrlAt(containerUrl) {
    return `${containerUrl}index.ttl`;
}
export function channelIriAt(containerUrl) {
    return `${indexUrlAt(containerUrl)}#this`;
}
export function dayFileAt(containerUrl, date) {
    return `${containerUrl}${dayPath(date)}/chat.ttl`;
}
export function messageIriAt(containerUrl, messageId, date) {
    return `${dayFileAt(containerUrl, date)}#${encodeURIComponent(messageId)}`;
}

// roomId-addressed (a chat in our own pod) — thin wrappers over the above.
export function chatIndexUrl(storageRoot, roomId) {
    return indexUrlAt(chatRootUrl(storageRoot, roomId));
}
export function chatChannelIri(storageRoot, roomId) {
    return channelIriAt(chatRootUrl(storageRoot, roomId));
}
export function chatDayUrl(storageRoot, roomId, date) {
    return dayFileAt(chatRootUrl(storageRoot, roomId), date);
}
export function messageIriFor(storageRoot, roomId, messageId, date) {
    return messageIriAt(chatRootUrl(storageRoot, roomId), messageId, date);
}

/** The channel resource: `<#this> a meeting:LongChat; dc:title "..." .` */
export function buildIndexTurtle(title) {
    return [
        `@prefix meeting: <${NS.meeting}>.`,
        `@prefix dc: <${NS.dc}>.`,
        '',
        '<#this>',
        '    a meeting:LongChat;',
        `    dc:title "${escapeTurtleLiteral(title || 'Proxion room')}" .`,
        '',
    ].join('\n');
}

/**
 * A SPARQL-Update body appending one message to a day file, matching how
 * SolidOS appends. Absolute IRIs throughout, so relative-reference resolution
 * inside a PATCH body cannot vary between servers.
 */
export function appendOps({ channelIri, messageIri, content, createdIso, makerIri, fromName, seq, replyToIri, proof }) {
    // Both link predicates: wf:message for the SolidOS databrowser, meeting:message
    // for the written spec and POD-CHAT. Absolute IRIs throughout.
    const inserts = [
        `${iriRef(channelIri)} ${iriRef(P.message)} ${iriRef(messageIri)} .`,
        `${iriRef(channelIri)} ${iriRef(P.wfMessage)} ${iriRef(messageIri)} .`,
        `${iriRef(messageIri)} ${iriRef(P.created)} "${escapeTurtleLiteral(createdIso)}"^^${iriRef(P.dateTime)} .`,
        `${iriRef(messageIri)} ${iriRef(P.content)} "${escapeTurtleLiteral(content)}" .`,
    ];
    // foaf:maker is an IRI node; omit rather than emit an empty value.
    if (makerIri) inserts.push(`${iriRef(messageIri)} ${iriRef(P.maker)} ${iriRef(makerIri)} .`);
    // The author's display name, covered by the signature below. Omitted when
    // empty (a signed empty name canonicalizes to '', matching a reader default).
    if (fromName) inserts.push(`${iriRef(messageIri)} ${iriRef(P.fromName)} "${escapeTurtleLiteral(fromName)}" .`);
    // #4: the shape's sec:proofValue signature; omitted when we cannot sign (no
    // key), leaving a valid unsigned message exactly like other Solid chat apps.
    if (proof) inserts.push(`${iriRef(messageIri)} ${iriRef(P.proofValue)} "${escapeTurtleLiteral(proof)}" .`);
    // D4: monotonic order hint (server-clock ms); omitted until known (set on echo).
    if (Number.isFinite(seq)) inserts.push(`${iriRef(messageIri)} ${iriRef(P.seq)} ${Math.trunc(seq)} .`);
    // R101.1: parent-to-reply sioc:has_reply (readers union the day files).
    if (replyToIri) inserts.push(`${iriRef(replyToIri)} ${iriRef(P.hasReply)} ${iriRef(messageIri)} .`);
    return { inserts };
}

// Serialize {inserts, deletes, where} triple-string arrays to a SPARQL-Update
// body. build*Patch keep their original output via this; pod.js's podRdfPatch
// serializes the SAME ops to N3 Patch when the server advertises it (R101).
function _sparqlFromOps({ inserts = [], deletes = [], where = [] }) {
    if (where.length) {
        return `DELETE { ${deletes.join(' ')} }\nINSERT { ${inserts.join(' ')} }\nWHERE  { ${where.join(' ')} }\n`;
    }
    const parts = [];
    if (deletes.length) parts.push(`DELETE DATA {\n  ${deletes.join('\n  ')}\n}`);
    if (inserts.length) parts.push(`INSERT DATA {\n  ${inserts.join('\n  ')}\n}`);
    return parts.join(' ;\n') + '\n';
}

/** SPARQL-Update body appending one message to a day file (delegates to appendOps). */
export function buildAppendPatch(args) { return _sparqlFromOps(appendOps(args)); }

/**
 * Triples for a reaction as a schema:LikeAction targeting a message (R101). The
 * action node has a deterministic IRI so the exact same triples can be inserted
 * on react and deleted on un-react. Returns an array of triple strings for the
 * generic patch builders. Pure.
 */
export function reactionActionTriples({ actionIri, msgIri, agentIri, emoji, proof }) {
    const a = iriRef(actionIri);
    const triples = [
        `${a} a ${iriRef(P.likeAction)} .`,
        `${a} ${iriRef(P.target)} ${iriRef(msgIri)} .`,
        `${a} ${iriRef(P.content)} "${escapeTurtleLiteral(emoji)}" .`,
    ];
    if (agentIri) triples.push(`${a} ${iriRef(P.agent)} ${iriRef(agentIri)} .`);
    // #5 Part C: the reactor's signature over { action, target, agent, emoji }, so
    // the reader can prove the agent field was not forged. Omitted when unsigned.
    if (proof) triples.push(`${a} ${iriRef(P.reactProof)} "${escapeTurtleLiteral(proof)}" .`);
    return triples;
}

/**
 * Triples cancelling a reaction, append-only (#4). Un-react used to DELETE the
 * LikeAction, which needs acl:Write; a member now only has acl:Append, so instead
 * we tombstone the action node with schema:dateDeleted, exactly like a withdrawn
 * message. The reader (parseReactionActions) treats a tombstoned action as no
 * longer active. Same deterministic actionIri as reactionActionTriples. Pure.
 */
export function reactionCancelTriples({ actionIri, canceledIso, proof }) {
    const triples = [`${iriRef(actionIri)} ${iriRef(P.dateDeleted)} "${escapeTurtleLiteral(canceledIso)}"^^${iriRef(P.dateTime)} .`];
    // #5 Part C: only the reaction's own agent may cancel it. The proof over
    // { action, canceledIso } lets the reader ignore a tombstone forged by anyone
    // else. Omitted when unsigned (an unsigned cancel is not honoured).
    if (proof) triples.push(`${iriRef(actionIri)} ${iriRef(P.cancelProof)} "${escapeTurtleLiteral(proof)}" .`);
    return triples;
}

/**
 * A SPARQL-Update body that adds the D4 order hint (px:seq) to an existing
 * message. Used to stamp the server-assigned order onto a message that was
 * written optimistically before the echo arrived. Idempotent in effect: writing
 * the same triple twice is a no-op in RDF; a caller that re-stamps a different seq
 * should DELETE first, but in practice the server order for a message is stable.
 */
export function seqOps({ messageIri, seq }) {
    if (!Number.isFinite(seq)) return { inserts: [] };
    return { inserts: [`${iriRef(messageIri)} ${iriRef(P.seq)} ${Math.trunc(seq)} .`] };
}
export function buildSeqPatch(args) {
    const ops = seqOps(args);
    return ops.inserts.length ? _sparqlFromOps(ops) : '';
}

/**
 * Order comparator for room history (D4). When BOTH messages carry a px:seq (the
 * gateway's single-clock order), compare by it: that is the skew-free order every
 * device agrees on. Otherwise fall back to timestamp, the existing behaviour, so a
 * message without a seq yet still sorts sensibly.
 */
export function compareByOrder(a, b) {
    const sa = a && Number.isFinite(a.seq) ? a.seq : null;
    const sb = b && Number.isFinite(b.seq) ? b.seq : null;
    if (sa !== null && sb !== null) return sa - sb;
    return String((a && a.timestamp) || '').localeCompare(String((b && b.timestamp) || ''));
}

/**
 * A SPARQL-Update body that rewrites a message's text in place (Phase B: edits).
 *
 * DELETE/INSERT ... WHERE, not DELETE DATA + INSERT DATA, on purpose: it replaces
 * whatever sioc:content the message currently carries without needing to know the
 * old value, so it is idempotent and safe under concurrent edits, always ending
 * with exactly one content triple holding the latest text.
 *
 * Chosen over the append-only dct:isReplacedBy replacement-node chain
 * deliberately: an in-place content swap shows the latest text in ANY Long Chat
 * reader, whereas whether a given reader follows a replacement chain is
 * unverified (the R67 wf:message lesson: do not assume a reader honours the
 * spec). The px: layer keeps full edit history; the shared copy just stays current.
 */
export function editOps({ messageIri, newContent }) {
    const m = iriRef(messageIri), c = iriRef(P.content);
    return {
        deletes: [`${m} ${c} ?old .`],
        inserts: [`${m} ${c} "${escapeTurtleLiteral(newContent)}" .`],
        where: [`${m} ${c} ?old .`],
    };
}
export function buildEditPatch(args) { return _sparqlFromOps(editOps(args)); }

/**
 * A SPARQL-Update body that soft-deletes a message (Phase B: deletes).
 *
 * Appends a schema:dateDeleted tombstone rather than removing the node, so the
 * append-only day file stays valid and other Solid apps can see the message was
 * withdrawn.
 *
 * #5 Part A: a tombstone is otherwise unauthenticated — any acl:Append member
 * could write one against another member's message and censor it. So the deleter
 * ALSO writes a px:deleteProof: a signature over { message IRI, deletedIso }. Our
 * reader honours the tombstone only when this proof verifies for an authorized
 * signer (the maker, or the room owner). A tombstone without a valid proof (an
 * unsigned delete from another Solid app, or a forged one) is IGNORED, so the
 * message stays visible — the safe posture. Omitted when we cannot sign, in which
 * case the tombstone is interop-only (other apps hide it; Proxion does not).
 */
export function deleteOps({ messageIri, deletedIso, proof }) {
    const inserts = [`${iriRef(messageIri)} ${iriRef(P.dateDeleted)} "${escapeTurtleLiteral(deletedIso)}"^^${iriRef(P.dateTime)} .`];
    if (proof) inserts.push(`${iriRef(messageIri)} ${iriRef(P.deleteProof)} "${escapeTurtleLiteral(proof)}" .`);
    return { inserts };
}
export function buildDeletePatch(args) { return _sparqlFromOps(deleteOps(args)); }

/**
 * WAC ACL for a shared chat container. Owner gets full control; each participant
 * gets Read + Append only, so a participant can POST a new message but cannot
 * overwrite or delete an existing statement. Verified against CSS 7.1.9: a second
 * WebID with this grant can PATCH-append to the day file in another user's pod.
 *
 * acl:default propagates the grant to contained resources (the day files),
 * including ones a participant creates on a new UTC day. This is the difference
 * between "can read the chat" and "can take part in the conversation".
 *
 * Integrity (#4): Append (not Write) is all a participant needs, because every
 * member operation is INSERT-only — a new message, its px:seq stamp, a reaction
 * add, an append-only un-react tombstone, and a soft-delete tombstone. Without
 * acl:Write a participant can no longer overwrite or delete another participant's
 * message or the container index; only the owner keeps Write. In-place content
 * edit (editOps) is a DELETE/INSERT that needs Write, so the ACL makes editing a
 * message effectively owner-only, which is intentional under this model.
 */
export function buildChatAcl(ownerWebId, participantWebIds, containerUrl) {
    const lines = [
        '@prefix acl: <http://www.w3.org/ns/auth/acl#>.',
        '',
        '<#owner> a acl:Authorization;',
        `    acl:agent ${iriRef(ownerWebId)};`,
        `    acl:accessTo ${iriRef(containerUrl)};`,
        `    acl:default ${iriRef(containerUrl)};`,
        '    acl:mode acl:Read, acl:Write, acl:Control.',
    ];
    const valid = [...new Set(participantWebIds || [])].filter(w => w && w !== ownerWebId);
    valid.forEach((webid, i) => {
        lines.push(
            '',
            `<#participant${i}> a acl:Authorization;`,
            `    acl:agent ${iriRef(webid)};`,
            `    acl:accessTo ${iriRef(containerUrl)};`,
            `    acl:default ${iriRef(containerUrl)};`,
            '    acl:mode acl:Read, acl:Append.',
        );
    });
    lines.push('');
    return lines.join('\n');
}

// ── Reading (Phase C) ────────────────────────────────────────────────────────

/** Normalise whatever shape a server returns for JSON-LD into a node array. */
function nodesOf(json) {
    if (!json) return [];
    if (Array.isArray(json)) return json;
    if (Array.isArray(json['@graph'])) return json['@graph'];
    return [json];
}

/** Read a predicate off an expanded JSON-LD node, tolerating scalar or array. */
function valuesOf(node, predicate) {
    const raw = node[predicate];
    if (raw == null) return [];
    return Array.isArray(raw) ? raw : [raw];
}

function firstLiteral(node, predicate) {
    for (const v of valuesOf(node, predicate)) {
        if (v && typeof v === 'object' && '@value' in v) return v['@value'];
        if (typeof v === 'string') return v;
    }
    return null;
}

function firstId(node, predicate) {
    for (const v of valuesOf(node, predicate)) {
        if (v && typeof v === 'object' && v['@id']) return v['@id'];
        if (typeof v === 'string') return v;
    }
    return null;
}

/**
 * Parse a Long Chat day document (fetched as JSON-LD) into Proxion-shaped
 * messages. This works on chats written by SolidOS or POD-CHAT as well as our
 * own, because it keys off the shared predicates rather than anything Proxion
 * specific. Where our px: terms happen to be present they fill in the extras
 * the shared vocabulary has no term for.
 */
/**
 * Merge pod-sourced messages into a locally-held list.
 *
 * Local entries win on id collision: a message we just sent is richer (it still
 * has reactions, reply context and the display name) than the same message read
 * back from the pod, where the shared vocabulary carries only text, author and
 * time. Ordering is by timestamp so history pulled from another app interleaves
 * correctly rather than being appended in a block.
 */
export function mergeLongChatMessages(local = [], fromPod = []) {
    const byId = new Map();
    for (const m of fromPod || []) if (m && m.message_id) byId.set(m.message_id, m);
    for (const m of local || []) if (m && m.message_id) byId.set(m.message_id, m);
    return [...byId.values()].sort(
        (a, b) => String(a.timestamp || '').localeCompare(String(b.timestamp || ''))
    );
}

/**
 * Reconcile a room's on-open history with the pod being AUTHORITATIVE for content
 * (PLAN_ROUND_69 D1). The opposite of mergeLongChatMessages' local-wins: here the
 * pod is the durable log, so for a message the pod has, the pod's content, deleted
 * state and timestamp win; local-only richer fields (reactions, reply context,
 * display name) are kept where the pod copy is blank. A local message the pod does
 * NOT have yet (an in-flight or failed send) is overlaid on top. A tombstoned
 * (schema:dateDeleted) message is dropped from the feed, which also removes a copy
 * shown optimistically from a local cache. Result is timestamp-ordered.
 *
 * Safety: with an empty or unreadable pod list this returns the local list
 * unchanged (every local message is "local-only"), so a pod outage never blanks a
 * room. The caller decides whether to call this at all (both pod reads failing =>
 * skip and keep local-first).
 */
export function reconcileRoomHistory(local = [], pod = []) {
    const localById = new Map();
    for (const m of local || []) if (m && m.message_id) localById.set(m.message_id, m);
    const out = [];
    const seen = new Set();
    for (const p of pod || []) {
        if (!p || !p.message_id || seen.has(p.message_id)) continue;
        seen.add(p.message_id);            // mark seen even when deleted, so a local copy is dropped too
        if (p.deleted) continue;           // tombstoned: reads as deleted, not shown
        const l = localById.get(p.message_id);
        // Pod wins for content/deleted/timestamp/author; keep local's extra fields
        // (which the shared vocabulary has no term for) where the pod copy is blank.
        out.push(l ? {
            ...l,
            content: p.content,
            deleted: false,
            timestamp: p.timestamp || l.timestamp,
            from_webid: p.from_webid || l.from_webid,
            from_display_name: p.from_display_name || l.from_display_name,
            // Pod's order hint wins when present (D4); keep the local one otherwise.
            ...(Number.isFinite(p.seq) ? { seq: p.seq } : {}),
            // #4: the pod copy is the authoritative content, so its signature verdict
            // wins too; carry it onto the merged message (own messages never show the
            // badge, so this only surfaces on others').
            ...(typeof p.sender_verified === 'boolean' ? { sender_verified: p.sender_verified } : {}),
        } : p);
    }
    for (const l of local || []) {
        if (l && l.message_id && !seen.has(l.message_id)) out.push(l);
    }
    out.sort(compareByOrder);   // D4: server order (px:seq) when known, else timestamp
    return out;
}

// Cap on @graph nodes walked from a (possibly foreign) day file: defence in depth
// alongside the byte cap on the fetched body in pod.podReadChatDayAt.
const MAX_LONGCHAT_NODES = 5000;

export function parseLongChatJsonLd(json, threadId = '') {
    const out = [];
    for (const node of nodesOf(json).slice(0, MAX_LONGCHAT_NODES)) {
        if (!node || typeof node !== 'object') continue;
        // A reaction is a schema:LikeAction that ALSO carries sioc:content (the
        // emoji), so skip it by type before the content check, or it would surface
        // as a stray message (and a tombstoned un-react as a "deleted message").
        // Reactions are read separately (parseReactionActions).
        const types = node['@type'];
        const typeList = Array.isArray(types) ? types : (types ? [types] : []);
        if (typeList.includes(P.likeAction)) continue;
        const content = firstLiteral(node, P.content);
        if (content == null) continue;          // not a message node
        const id = String(node['@id'] || '');
        // #5 Part A: a schema:dateDeleted tombstone is EXTRACTED here but NOT
        // honoured yet. Under acl:Append any member can write a tombstone against
        // anyone's message, so trusting the raw tombstone would let a member censor
        // a signed message. The message therefore stays VISIBLE at parse time;
        // verifyLongChatMessages marks it deleted only when an accompanying
        // px:deleteProof verifies for an authorized signer (the maker, or owner).
        // An unsigned/forged tombstone (or one from another Solid app) is ignored.
        const deletedAt = firstLiteral(node, P.dateDeleted);
        const deleteProof = firstLiteral(node, P.deleteProof);
        const seqRaw = firstLiteral(node, P.seq);
        const seq = seqRaw == null ? undefined : Number(seqRaw);
        // #4: the shape's message signature, if present. Extracted here; verified
        // (and authorized signer -> maker) asynchronously by verifyLongChatMessages
        // on the read path, since that needs a pod fetch. Absent on SolidOS /
        // POD-CHAT messages, which read as unverified but are never dropped.
        const proof = firstLiteral(node, P.proofValue);
        out.push({
            message_id: id.includes('#') ? id.slice(id.lastIndexOf('#') + 1) : id,
            // The full message IRI is the signed `id` field; keep it so the proof
            // can be re-checked over the exact bytes that were signed.
            iri: id,
            thread_id: threadId,
            content,
            // Not deleted until an authorized deletion is proven (see above).
            deleted: false,
            deleted_at: deletedAt || null,
            delete_proof: deleteProof || null,
            proof: proof || null,
            // Default to unverified; verifyLongChatMessages upgrades a valid,
            // authorized signature to true. Marked, never a reason to drop.
            sender_verified: false,
            timestamp: firstLiteral(node, P.created) || null,
            from_webid: firstId(node, P.maker) || '',
            // D4 order hint (ours). Absent on SolidOS / POD-CHAT messages; those
            // fall back to timestamp order via compareByOrder.
            ...(Number.isFinite(seq) ? { seq } : {}),
            // Extras only Proxion writes; absent on SolidOS / POD-CHAT messages.
            from_display_name: firstLiteral(node, NS.px + 'fromName') || '',
            content_type: firstLiteral(node, NS.px + 'contentType') || 'text',
            source: 'longchat',
        });
    }
    out.sort(compareByOrder);
    return out;
}

/**
 * Authenticate parsed Long Chat messages against their sec:proofValue (#4).
 *
 * For each message that carries a proof, the signature is checked over the
 * message's core fields (id, created, content, maker), and the recovered signer
 * did:key is authorized to speak for foaf:maker by fetching the maker's published
 * signer identity from THEIR OWN pod (the trust anchor an attacker cannot write
 * to, exactly as a DM is verified). A message with no proof, an invalid
 * signature, or a signer the maker has not published stays sender_verified:false
 * — it is marked, never dropped, so unsigned messages from other Solid chat apps
 * still show. A message whose content was edited or tombstoned after signing no
 * longer matches its proof and reads as unverified, which is honest.
 *
 * #5 Part A: this is also where an authenticated soft-delete is honoured. A parsed
 * message carrying a schema:dateDeleted tombstone stays visible until its
 * px:deleteProof verifies over { iri, deletedIso } AND the signer is authorized:
 * the message's own foaf:maker (self-delete, the MVP that closes cross-member
 * censorship) or, when `ownerWebId` is supplied by the caller, the room owner
 * (owner moderation). Only then is the message marked deleted and its content
 * blanked; an unsigned, forged or foreign tombstone leaves the message visible.
 *
 * Async and dependency-injected (fetchPeerSigner, peerPodRoot) so it stays
 * unit-testable without a live pod. Mutates each message in place and returns the
 * same array. peerPodRoot(webid) -> pod root; fetchPeerSigner(root) -> { signer }.
 */
export async function verifyLongChatMessages(messages, { fetchPeerSigner, peerPodRoot, ownerWebId } = {}) {
    if (typeof fetchPeerSigner !== 'function' || typeof peerPodRoot !== 'function') return messages || [];
    const signerCache = new Map();   // pod root -> published signer doc (dedupe fetches within a read)
    // The published signer did:key for a WebID (or null), memoised per read.
    const publishedSignerFor = async (webid) => {
        const root = webid && peerPodRoot(webid);
        if (!root) return null;
        if (!signerCache.has(root)) signerCache.set(root, await fetchPeerSigner(root));
        const iddoc = signerCache.get(root);
        return (iddoc && iddoc.signer) || null;
    };
    for (const m of messages || []) {
        if (!m || !m.iri) continue;
        // Message signature: authorizes signer -> foaf:maker (covers from_display_name).
        if (m.proof && m.from_webid) {
            const signer = await verifyLongChatProof(
                { id: m.iri, created: m.timestamp || '', content: m.content || '', maker: m.from_webid, from_display_name: m.from_display_name || '' },
                m.proof,
            );
            if (signer && signer === await publishedSignerFor(m.from_webid)) m.sender_verified = true;
        }
        // Soft-delete: honour the tombstone only when its proof verifies for an
        // authorized signer (the maker, or the room owner when known).
        if (m.deleted_at && m.delete_proof) {
            const signer = await verifyLongChatDeleteProof({ iri: m.iri, deletedIso: m.deleted_at }, m.delete_proof);
            if (signer) {
                const makerSigner = await publishedSignerFor(m.from_webid);
                const ownerSigner = ownerWebId ? await publishedSignerFor(ownerWebId) : null;
                if ((makerSigner && signer === makerSigner) || (ownerSigner && signer === ownerSigner)) {
                    m.deleted = true;
                    m.content = '';
                }
            }
        }
    }
    return messages || [];
}

/**
 * Read the reactions in a Long Chat day document (#4, #5 Part C). Each reaction is
 * a schema:LikeAction targeting a message; an append-only un-react tombstones that
 * action with schema:dateDeleted (see reactionCancelTriples). Pure.
 *
 * This returns EVERY candidate like-action — including tombstoned ones — with its
 * proof fields extracted but NOTHING trusted yet. The agent field is only a claim
 * (any member can Append a LikeAction naming anyone as agent), and a tombstone is
 * only a claim (any member can Append a schema:dateDeleted onto anyone's action).
 * verifyReactionActions authenticates both and returns the trustworthy set; a
 * caller that wants only authenticated, active reactions must run it.
 */
export function parseReactionActions(json) {
    const out = [];
    for (const node of nodesOf(json).slice(0, MAX_LONGCHAT_NODES)) {
        if (!node || typeof node !== 'object') continue;
        const types = node['@type'];
        const typeList = Array.isArray(types) ? types : (types ? [types] : []);
        if (!typeList.includes(P.likeAction)) continue;
        out.push({
            action_iri: String(node['@id'] || ''),
            target_iri: firstId(node, P.target) || '',
            emoji: firstLiteral(node, P.content) || '',
            agent: firstId(node, P.agent) || '',
            proof: firstLiteral(node, P.reactProof) || null,
            canceled_at: firstLiteral(node, P.dateDeleted) || null,
            cancel_proof: firstLiteral(node, P.cancelProof) || null,
            // Default to unverified; verifyReactionActions upgrades a valid,
            // agent-authorized reaction to true.
            verified: false,
        });
    }
    return out;
}

/**
 * Authenticate parsed reactions against their proofs (#5 Part C). For each
 * candidate:
 *   * the react proof must verify over { action, target, agent, emoji } AND the
 *     signer must be the one the AGENT published at their own pod — otherwise the
 *     reaction is forged (agent field spoofed) and dropped.
 *   * a cancel is honoured only when its proof verifies over { action,
 *     canceledIso } for the SAME agent's published signer — so only the reactor can
 *     un-react. A forged cancel by anyone else is ignored and the reaction stays.
 *
 * Returns the trustworthy, ACTIVE reactions ({ action_iri, target_iri, emoji,
 * agent, verified:true }). Async and dependency-injected like
 * verifyLongChatMessages: peerPodRoot(agent) -> pod root; fetchPeerSigner(root) ->
 * { signer }. Without the deps it returns [] (nothing can be authenticated).
 */
export async function verifyReactionActions(reactions, { fetchPeerSigner, peerPodRoot } = {}) {
    if (typeof fetchPeerSigner !== 'function' || typeof peerPodRoot !== 'function') return [];
    const signerCache = new Map();
    const publishedSignerFor = async (webid) => {
        const root = webid && peerPodRoot(webid);
        if (!root) return null;
        if (!signerCache.has(root)) signerCache.set(root, await fetchPeerSigner(root));
        const iddoc = signerCache.get(root);
        return (iddoc && iddoc.signer) || null;
    };
    const out = [];
    for (const r of reactions || []) {
        if (!r || !r.action_iri || !r.agent || !r.proof) continue;   // unsigned/foreign: not trusted
        const agentSigner = await publishedSignerFor(r.agent);
        if (!agentSigner) continue;
        const signer = await verifyLongChatReactionProof(
            { action: r.action_iri, target: r.target_iri, agent: r.agent, emoji: r.emoji }, r.proof,
        );
        if (!signer || signer !== agentSigner) continue;             // forged agent: dropped
        // An authenticated cancel by the same agent withdraws the reaction.
        if (r.canceled_at && r.cancel_proof) {
            const csigner = await verifyLongChatCancelProof({ action: r.action_iri, canceledIso: r.canceled_at }, r.cancel_proof);
            if (csigner && csigner === agentSigner) continue;        // genuinely cancelled
        }
        r.verified = true;
        out.push(r);
    }
    return out;
}
