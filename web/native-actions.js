import { nativeMessageRef, nativeMessageIdForRef } from './native-message-ref.js';
import { encodeNativeAction } from './native-action-codec.js';

// Source intent precedes local deletion and encryption. Recovery is deliberately
// independent of the original row: deleting history must not cancel a header.
export function createNativeActions({ getScope, getGeneration, isActive, contextForView, getMessage,
    listJobs, readState, apply, references, queue, transmit, onApplied = async () => {} }) {
    const pending = new Map();
    async function save(view, id, operation) {
        const scope = getScope(), generation = getGeneration(), context = contextForView(view);
        const current = () => isActive() && scope === getScope() && generation === getGeneration()
            && JSON.stringify(contextForView(view)) === JSON.stringify(context);
        if (!context || !current()) return { ok: false, reason: 'unavailable' };
        const actorContext = { ...context, actorWebId: context.selfWebId };
        const key = JSON.stringify([scope, context.threadId, id]);
        const task = (pending.get(key) || Promise.resolve()).catch(() => {}).then(async () => {
            const original = await getMessage(id);
            if (!current() || original && (!nativeMessageRef(original, context) || original.deleted)
                || !original && operation !== 'unpin') return { ok: false, reason: 'unavailable' };
            // Ownership and operation bounds fail before any preflight or intent.
            if (original) encodeNativeAction(original, context, { messageId: 'validate', operation, clock: 1 });
            const intents = (await listJobs()).filter(job => job.scope === scope && job.nativeControl === 'action'
                && job.record?.thread_id === context.threadId && job.record?.from_webid === context.selfWebId);
            if (!current()) return { ok: false, reason: 'account_changed' };
            for (const intent of intents.sort((a, b) => a.record.native_action.clock - b.record.native_action.clock)) {
                if (await apply(intent.record.native_action, actorContext, { current }) !== true || !current()) return { ok: false };
            }
            const rows = await readState(actorContext, { current });
            if (!rows || !current()) return { ok: false };
            const previous = rows.find(row => row.message_id === id);
            if (previous?.deletedEveryone || previous?.deletedLocal) return { ok: false, reason: 'deleted' };
            let retained = null;
            if (!original) {
                retained = { v: 1, kind: 'native_file', id: id.split(':').at(-1), author: previous?.from_webid === context.selfWebId ? 'actor' : 'peer' };
                if (!previous?.pin || previous.thread_id !== context.threadId
                    || ![context.selfWebId, context.peerWebId].includes(previous.from_webid)
                    || nativeMessageIdForRef(retained, actorContext) !== id) return { ok: false, reason: 'unavailable' };
            }
            if (operation !== 'delete' && !!previous?.pin?.active === (operation === 'pin')) return { ok: true };
            const input = { messageId: crypto.randomUUID(), operation, clock: (previous?.pin?.clock || 0) + 1 };
            const encoded = retained ? await references.prepareRetainedUnpin(retained, view, input, current)
                : await references.prepareAction(original, view, input, current);
            if (!current()) return { ok: false, reason: 'account_changed' };
            const job = await queue(encoded, actorContext, current);
            if (!job || !current() || await apply(encoded.body, actorContext, { current }) !== true || !current()) return { ok: false };
            await onApplied(actorContext, current);
            if (current()) await transmit(job.id);
            return { ok: current() };
        }).catch(() => ({ ok: false, reason: 'unavailable' }));
        pending.set(key, task);
        try { return await task; } finally { if (pending.get(key) === task) pending.delete(key); }
    }
    return { save };
}
