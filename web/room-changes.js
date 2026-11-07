// Signed, append-only message revisions. Original Long Chat statements and
// proofs remain intact; an unsigned or unauthorized change has no effect.
import { verifyLongChatChange } from './dmsig.js';
import { NS, escapeTurtleLiteral, iriRef } from './longchat.js';

export const CHANGE_TYPE = NS.px + 'MessageChange';
export const CHANGE_PAYLOAD = NS.px + 'changePayload';
const MAX_CHANGES = 1000;
export const isReactionChange = kind => kind === 'react' || kind === 'unreact';
export function isRoomReaction(value) {
    return typeof value === 'string' && value.length > 0 && value.length <= 64
        && new TextEncoder().encode(value).length <= 128 && !/[\s\x00-\x1f\x7f]/u.test(value)
        && !Object.hasOwn(Object.prototype, value) && value !== 'prototype';
}
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const compareChanges = (a, b) => compare(a.created, b.created) || compare(a.id, b.id) || compare(a.proof, b.proof);

export function changeOps(change) {
    return { inserts: [
        `${iriRef(change.id)} a ${iriRef(CHANGE_TYPE)} .`,
        `${iriRef(change.id)} ${iriRef(CHANGE_PAYLOAD)} "${escapeTurtleLiteral(JSON.stringify(change))}" .`,
    ] };
}

export function parseMessageChanges(json) {
    const nodes = Array.isArray(json) ? json : Array.isArray(json?.['@graph']) ? json['@graph'] : [json];
    const changes = [];
    for (const node of nodes.slice(0, 20000)) {
        const values = node?.[CHANGE_PAYLOAD];
        for (const value of (Array.isArray(values) ? values : values ? [values] : [])) {
            if (changes.length >= MAX_CHANGES) return changes;
            const raw = typeof value === 'string' ? value : value?.['@value'];
            if (typeof raw !== 'string' || raw.length > 65536) continue;
            try {
                const c = JSON.parse(raw);
                if (c.id !== node['@id'] || !['edit', 'delete', 'react', 'unreact'].includes(c.kind)) continue;
                if (![c.id, c.target, c.actor, c.created, c.content, c.previous, c.proof].every(v => typeof v === 'string')) continue;
                if (c.id.length > 4096 || c.target.length > 4096 || c.actor.length > 2048 || c.previous.length > 4096 || c.proof.length > 512) continue;
                if (new TextEncoder().encode(c.content).length > 16384) continue;
                if (!Number.isFinite(Date.parse(c.created)) || new Date(c.created).toISOString() !== c.created) continue;
                if (c.kind === 'delete' && c.content !== '') continue;
                if (isReactionChange(c.kind) && !isRoomReaction(c.content)) continue;
                changes.push(c);
            } catch { /* malformed changes cannot alter the message */ }
        }
    }
    return changes;
}

export async function applyMessageChanges(messages, json, { ownerWebId, fetchPeerSigner, peerPodRoot }) {
    if (typeof fetchPeerSigner !== 'function' || typeof peerPodRoot !== 'function') return messages;
    const byTarget = new Map(messages.map(m => [m.iri, m]));
    const signers = new Map(), valid = new Map();
    for (const change of parseMessageChanges(json)) {
        const message = byTarget.get(change.target);
        if (!message || !change.id.startsWith(change.target.split('#')[0] + '#change-')) continue;
        if (!isReactionChange(change.kind) && change.actor !== message.from_webid
            && !(change.kind === 'delete' && change.actor === ownerWebId)) continue;
        const recovered = await verifyLongChatChange(change);
        if (!recovered) continue;
        const root = peerPodRoot(change.actor);
        if (!root) continue;
        if (!signers.has(root)) {
            try { signers.set(root, await fetchPeerSigner(root)); }
            catch { signers.set(root, null); }
        }
        const signer = signers.get(root)?.signer;
        if (!signer || recovered !== signer) continue;
        if (!valid.has(change.target)) valid.set(change.target, []);
        valid.get(change.target).push(change);
    }
    for (const message of messages) {
        message.reactions = {};
        message.reaction_versions = {};
    }
    for (const [target, changes] of valid) {
        const message = byTarget.get(target);
        changes.sort(compareChanges);
        const reactions = new Map();
        for (const c of changes.filter(operation => isReactionChange(operation.kind))) {
            const key = JSON.stringify([c.content, c.actor]);
            reactions.set(key, c);
        }
        const active = new Map();
        for (const c of reactions.values()) {
            if (!message.reaction_versions[c.content]) message.reaction_versions[c.content] = {};
            message.reaction_versions[c.content][c.actor] = { id: c.id, created: c.created, active: c.kind === 'react' };
            if (c.kind === 'react') {
                if (!active.has(c.content)) active.set(c.content, []);
                active.get(c.content).push(c.actor);
            }
        }
        message.reactions = Object.fromEntries([...active].sort(([a], [b]) => compare(a, b))
            .map(([emoji, actors]) => [emoji, actors.sort(compare)]));
        if (message.deleted) message.reactions = {};
        const revisions = changes.filter(c => !isReactionChange(c.kind));
        if (!revisions.length) continue;
        // Withdrawal is permanent: an edit, regardless of timestamp, cannot
        // bring a message back after its author or room owner deletes it.
        const deleted = revisions.filter(c => c.kind === 'delete').at(-1);
        const latest = deleted || revisions.at(-1);
        if (message.deleted && !deleted) continue;
        message.revision_id = latest.id;
        message.change_actor = latest.actor;
        if (deleted) {
            message.deleted = true;
            message.deleted_at = deleted.created;
            message.content = '';
            message.reactions = {};
        } else {
            message.content = latest.content;
            message.edited_at = latest.created;
            message.edited_by = latest.actor;
        }
    }
    return messages;
}
