// typeindex.js — Solid Type Index (PLAN_ROUND_70 Track D). Pure protocol: build
// the registrations that make a Proxion chat DISCOVERABLE by other Solid apps, and
// parse an index to find chats another app registered.
//
// Spec: https://solid.github.io/type-indexes/ (v1.0.0). A WebID profile links to a
// public type index (solid:publicTypeIndex); the index holds solid:TypeRegistration
// entries mapping a class (solid:forClass) to where its instances live
// (solid:instanceContainer). We register meeting:LongChat -> the chat's container,
// so a generic Solid app can enumerate a user's chats instead of needing a URL.
//
// Everything user/pod-derived is wrapped with iriRef, the same Turtle-injection
// boundary the Long Chat writer uses.
import { iriRef, NS as LC_NS } from './longchat.js';

export const NS = Object.freeze({
    solid: 'http://www.w3.org/ns/solid/terms#',
    rdf: 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
    meeting: LC_NS.meeting,      // meeting:LongChat, the class we register
});

export const CHAT_CLASS = NS.meeting + 'LongChat';

// A stable, collision-resistant, injection-free fragment id for a container's
// registration (djb2 -> hex). Deterministic so re-registering is idempotent and
// deregistering can DELETE the exact same triples.
export function registrationId(containerUrl) {
    let h = 5381;
    const s = String(containerUrl || '');
    for (let i = 0; i < s.length; i++) h = (((h << 5) + h) ^ s.charCodeAt(i)) >>> 0;
    return 'reg-' + h.toString(16);
}

/** The empty public type index document (Turtle), created when the pod has none. */
export function buildEmptyTypeIndex() {
    return [
        `@prefix solid: <${NS.solid}>.`,
        '',
        '<> a solid:TypeIndex, solid:ListedDocument.',
        '',
    ].join('\n');
}

// The three triples of one registration. Shared by register (INSERT DATA) and
// deregister (DELETE DATA) so the delete matches exactly.
function _registrationTriples(indexUrl, containerUrl, forClass) {
    const reg = iriRef(`${indexUrl}#${registrationId(containerUrl)}`);
    return [
        `  ${reg} ${iriRef(NS.rdf + 'type')} ${iriRef(NS.solid + 'TypeRegistration')} .`,
        `  ${reg} ${iriRef(NS.solid + 'forClass')} ${iriRef(forClass)} .`,
        `  ${reg} ${iriRef(NS.solid + 'instanceContainer')} ${iriRef(containerUrl)} .`,
    ].join('\n');
}

/** SPARQL-Update to register a chat container for meeting:LongChat. Idempotent. */
export function buildRegisterPatch({ indexUrl, containerUrl, forClass = CHAT_CLASS }) {
    return `INSERT DATA {\n${_registrationTriples(indexUrl, containerUrl, forClass)}\n}\n`;
}

/** SPARQL-Update to remove a chat's registration (exact triples). */
export function buildDeregisterPatch({ indexUrl, containerUrl, forClass = CHAT_CLASS }) {
    return `DELETE DATA {\n${_registrationTriples(indexUrl, containerUrl, forClass)}\n}\n`;
}

/** SPARQL-Update linking a profile to its public type index. */
export function buildProfileLinkPatch({ webId, indexUrl }) {
    return (
        `INSERT DATA {\n  ${iriRef(webId)} ${iriRef(NS.solid + 'publicTypeIndex')} ` +
        `${iriRef(indexUrl)} .\n}\n`
    );
}

// ── Reading ──────────────────────────────────────────────────────────────────

function nodesOf(json) {
    if (!json) return [];
    if (Array.isArray(json)) return json;
    if (Array.isArray(json['@graph'])) return json['@graph'];
    return [json];
}
function idsOf(node, predicate) {
    const raw = node[predicate];
    if (raw == null) return [];
    const arr = Array.isArray(raw) ? raw : [raw];
    return arr
        .map(v => (v && typeof v === 'object') ? v['@id'] : (typeof v === 'string' ? v : null))
        .filter(Boolean);
}

/** The `solid:publicTypeIndex` IRI from a profile document (JSON-LD). */
export function parsePublicTypeIndex(json, webId = '') {
    for (const node of nodesOf(json)) {
        if (!node || typeof node !== 'object') continue;
        if (webId && String(node['@id'] || '') !== webId) continue;
        const [url] = idsOf(node, NS.solid + 'publicTypeIndex');
        if (url) return url;
    }
    // Fall back to any node carrying the predicate (some servers frame differently).
    for (const node of nodesOf(json)) {
        const [url] = idsOf(node || {}, NS.solid + 'publicTypeIndex');
        if (url) return url;
    }
    return null;
}

/**
 * Container URLs registered for a class (default meeting:LongChat) in a type index
 * document (JSON-LD). These are chats another Solid app said live in this pod.
 */
export function parseRegisteredContainers(json, forClass = CHAT_CLASS) {
    const out = [];
    const seen = new Set();
    for (const node of nodesOf(json)) {
        if (!node || typeof node !== 'object') continue;
        if (!idsOf(node, NS.solid + 'forClass').includes(forClass)) continue;
        for (const c of idsOf(node, NS.solid + 'instanceContainer')) {
            if (c && !seen.has(c)) { seen.add(c); out.push(c); }
        }
    }
    return out;
}
