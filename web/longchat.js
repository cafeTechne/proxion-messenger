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
    // D4: a per-message monotonic order hint (px:, ours only) so a user's devices
    // agree on order despite client clock skew. Not part of the shared vocabulary.
    seq: NS.px + 'seq',
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
export function buildAppendPatch({ channelIri, messageIri, content, createdIso, makerIri, seq }) {
    const triples = [
        // Both link predicates: wf:message for the SolidOS databrowser,
        // meeting:message for the written spec and POD-CHAT.
        `  ${iriRef(channelIri)} ${iriRef(P.message)} ${iriRef(messageIri)} .`,
        `  ${iriRef(channelIri)} ${iriRef(P.wfMessage)} ${iriRef(messageIri)} .`,
        `  ${iriRef(messageIri)} ${iriRef(P.created)} "${escapeTurtleLiteral(createdIso)}"^^${iriRef(P.dateTime)} .`,
        `  ${iriRef(messageIri)} ${iriRef(P.content)} "${escapeTurtleLiteral(content)}" .`,
    ];
    // foaf:maker is an IRI node. Omit it entirely rather than emit an empty or
    // literal value when the author has no WebID or did.
    if (makerIri) {
        triples.push(`  ${iriRef(messageIri)} ${iriRef(P.maker)} ${iriRef(makerIri)} .`);
    }
    // D4: a monotonic order hint (the gateway's server-clock time as epoch ms), so
    // this device's messages order correctly against the SAME user's other devices
    // regardless of client clock skew. Omitted when not known yet (set on echo).
    if (Number.isFinite(seq)) {
        triples.push(`  ${iriRef(messageIri)} ${iriRef(P.seq)} ${Math.trunc(seq)} .`);
    }
    return `INSERT DATA {\n${triples.join('\n')}\n}\n`;
}

/**
 * A SPARQL-Update body that adds the D4 order hint (px:seq) to an existing
 * message. Used to stamp the server-assigned order onto a message that was
 * written optimistically before the echo arrived. Idempotent in effect: writing
 * the same triple twice is a no-op in RDF; a caller that re-stamps a different seq
 * should DELETE first, but in practice the server order for a message is stable.
 */
export function buildSeqPatch({ messageIri, seq }) {
    if (!Number.isFinite(seq)) return '';
    return `INSERT DATA {\n  ${iriRef(messageIri)} ${iriRef(P.seq)} ${Math.trunc(seq)} .\n}\n`;
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
export function buildEditPatch({ messageIri, newContent }) {
    const m = iriRef(messageIri);
    const c = iriRef(P.content);
    return [
        `DELETE { ${m} ${c} ?old . }`,
        `INSERT { ${m} ${c} "${escapeTurtleLiteral(newContent)}" . }`,
        `WHERE  { ${m} ${c} ?old . }`,
        '',
    ].join('\n');
}

/**
 * A SPARQL-Update body that soft-deletes a message (Phase B: deletes).
 *
 * Appends a schema:dateDeleted tombstone rather than removing the node, so the
 * append-only day file stays valid and other Solid apps can see the message was
 * withdrawn. Our reader blanks the content of a tombstoned message on read.
 */
export function buildDeletePatch({ messageIri, deletedIso }) {
    return (
        `INSERT DATA {\n  ${iriRef(messageIri)} ${iriRef(P.dateDeleted)} ` +
        `"${escapeTurtleLiteral(deletedIso)}"^^${iriRef(P.dateTime)} .\n}\n`
    );
}

/**
 * WAC ACL for a shared chat container. Owner gets full control; each participant
 * gets Read + Write + Append so they can POST. Verified against CSS 7.1.9: a
 * second WebID with this grant can PATCH the day file in another user's pod.
 *
 * acl:default propagates the grant to contained resources (the day files),
 * including ones a participant creates on a new UTC day. This is the difference
 * between "can read the chat" and "can take part in the conversation".
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
            '    acl:mode acl:Read, acl:Write, acl:Append.',
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
        } : p);
    }
    for (const l of local || []) {
        if (l && l.message_id && !seen.has(l.message_id)) out.push(l);
    }
    out.sort(compareByOrder);   // D4: server order (px:seq) when known, else timestamp
    return out;
}

export function parseLongChatJsonLd(json, threadId = '') {
    const out = [];
    for (const node of nodesOf(json)) {
        if (!node || typeof node !== 'object') continue;
        const content = firstLiteral(node, P.content);
        if (content == null) continue;          // not a message node
        const id = String(node['@id'] || '');
        // Phase B: a schema:dateDeleted tombstone means the message was withdrawn
        // (by us or another app). It reads as deleted with no content, never as
        // stale text; callers can drop it or show a tombstone.
        const deletedAt = firstLiteral(node, P.dateDeleted);
        const seqRaw = firstLiteral(node, P.seq);
        const seq = seqRaw == null ? undefined : Number(seqRaw);
        out.push({
            message_id: id.includes('#') ? id.slice(id.lastIndexOf('#') + 1) : id,
            thread_id: threadId,
            content: deletedAt != null ? '' : content,
            deleted: deletedAt != null,
            deleted_at: deletedAt || null,
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
