import { newReactionToken } from './e2e-secondary.js';
import { nativeMessageRef } from './native-message-ref.js';

// Source intent is durable before local optimistic state and encryption. A
// restart reconciles retained intents before assigning the next sequence.
export function createNativeReactions({ store, references, getScope, getGeneration, isActive, contextForView,
    getMessage, listJobs, queue, transmit, onApplied = async () => {} }) {
    const pending = new Map();
    async function save(view, id, emoji, add) {
        const scope = getScope(), generation = getGeneration(), context = contextForView(view);
        const current = () => isActive() && scope === getScope() && generation === getGeneration()
            && JSON.stringify(contextForView(view)) === JSON.stringify(context);
        if (!context || !current() || typeof add !== 'boolean') return { ok: false };
        const actorContext = { ...context, actorWebId: context.selfWebId };
        const key = JSON.stringify([scope, context.threadId, id, emoji]);
        const task = (pending.get(key) || Promise.resolve()).catch(() => {}).then(async () => {
            if (!current()) return { ok: false, reason: 'account_changed' };
            const original = await getMessage(id);
            if (!current() || !nativeMessageRef(original, context) || !original.file?.data_b64 || original.file.unavailable || original.deleted) return { ok: false, reason: 'unavailable' };
            const intents = (await listJobs()).filter(job => job.scope === scope && job.nativeControl === 'reaction'
                && job.record?.thread_id === context.threadId && job.record?.from_webid === context.selfWebId);
            if (!current()) return { ok: false, reason: 'account_changed' };
            for (const intent of intents.sort((a, b) => a.record.native_reaction.seq - b.record.native_reaction.seq)) {
                if (!current() || await store.apply(intent.record.native_reaction, actorContext, current) !== true) return { ok: false };
            }
            const rows = await store.rows(actorContext, current);
            if (!rows || !current()) return { ok: false };
            const previous = rows.find(row => row.target === id && row.actorWebId === context.selfWebId && row.emoji === emoji);
            if ((previous?.op === 'add') === add) return { ok: true };
            const encoded = await references.prepareReaction(original, view, { messageId: crypto.randomUUID(),
                token: previous?.tok || newReactionToken(), emoji, sequence: (previous?.seq || 0) + 1, add }, current);
            if (!current()) return { ok: false, reason: 'account_changed' };
            const job = await queue(encoded, actorContext, current);
            if (!job || !current()) return { ok: false };
            if (await store.apply(encoded.body, actorContext, current) !== true || !current()) return { ok: false };
            await onApplied(actorContext, current);
            if (current()) await transmit(job.id);
            return { ok: current() };
        }).catch(() => ({ ok: false }));
        pending.set(key, task);
        try { return await task; } finally { if (pending.get(key) === task) pending.delete(key); }
    }
    return { save };
}
