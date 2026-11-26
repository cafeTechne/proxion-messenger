import { dmEnvelopeFingerprint } from './dm-receipts.js';
import { encodeNativeEdit, decodeNativeEdit } from './native-edit-codec.js';
import { nativeMessageRef } from './native-message-ref.js';

const SHA = /^[a-f0-9]{64}$/;
const PUB = /^[A-Za-z0-9_-]{43}$/;
const header = enc => ({ e2e: true, e2e_v: 1, content: enc.ciphertext, nonce: enc.nonce,
    msg_num: enc.msgNum, pn: enc.pn, ratchet_pub: enc.ratchetPub });
const b64u = hex => btoa(String.fromCharCode(...hex.match(/../g).map(byte => parseInt(byte, 16))))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

// Small caption plans use the existing encrypted send-job journal, in a
// distinct scope. The plan precedes ratchet preparation, and a prepared edit
// retains its exact send id/header until both history and delivery commit.
export function createNativeEditSender({ e2e, getScope, getGeneration = () => 0, isActive = () => true, isInspectable = isActive,
    contextForView, getPeerPub, getMessage, isDeleted = async () => false, capability, send, applyEdit, onApplied = () => {}, onState = () => {} }) {
    let epoch = 0;
    const jobs = new Map(), queues = new Map();
    let intents = Promise.resolve();
    const context = (readOnly = false) => {
        const scope = 'native-edit:' + getScope(), stamp = epoch, generation = getGeneration();
        return { scope, current: () => (readOnly ? isInspectable() : isActive()) && stamp === epoch && generation === getGeneration() && scope === 'native-edit:' + getScope() };
    };
    async function save(job, ctx) {
        const next = { ...job, revision: Math.max(job.revision || 0, jobs.get(job.id)?.revision || 0) + 1 };
        if (!ctx.current()) throw Error('Native edit context changed');
        await e2e.saveDmSendJob(next, ctx.current);
        if (!ctx.current()) throw Error('Native edit context changed');
        jobs.set(next.id, next); return next;
    }
    async function restore(ctx) {
        const stored = await e2e.listDmSendJobs();
        if (!ctx.current()) return false;
        for (const job of stored.filter(row => row.kind === 'native-edit' && row.scope === ctx.scope)) {
            if (!/^ne-[A-Za-z0-9_-]{1,120}$/.test(job.id || '') || !SHA.test(job.sendId || '')
                || !PUB.test(job.peerPub || '') || !decodeNativeEdit(job.plaintext, job.event)
                || job.event.message_id !== job.id || job.event.from_webid !== job.context?.selfWebId
                || job.event.recipient_pub !== job.peerPub || job.peer !== job.context?.peerWebId
                || job.messageId !== `native:${job.context.selfWebId}:${job.event.file_id}`) continue;
            if ((jobs.get(job.id)?.revision || 0) <= (job.revision || 0)) jobs.set(job.id, job);
        }
        return true;
    }
    async function serialized(id, work) {
        const ctx = context(), key = JSON.stringify([ctx.scope, id]);
        if (!queues.has(key) && queues.size >= 10) return false;
        const run = (queues.get(key) || Promise.resolve()).catch(() => {}).then(() => ctx.current() ? work(ctx) : false);
        queues.set(key, run);
        try { return await run; }
        finally { if (queues.get(key) === run) queues.delete(key); }
    }
    async function complete(job, current, ctx) {
        if (!current() || !job.headerCommitted) return false;
        const remove = async () => {
            if (!current() || await e2e.removeDmSendJob(job.id, current) === false || !current()) return false;
            jobs.delete(job.id); onState(job.id, 'committed'); return true;
        };
        if (job.committed && job.historySaved && await e2e.finishDmSend(job.peer, job.sendId, current)) return remove();
        if (!await e2e.releaseDmSendHold(job.peer, job.sendId, current) || !current()) return false;
        if (!job.committed) return true;
        if (!job.historySaved) {
            if (!await applyEdit(job.plaintext, job.event, { ...job.context, actorWebId: job.context.selfWebId }, { current }) || !current()) return false;
            job = await save({ ...job, historySaved: true }, ctx);
        }
        if (!current() || !await e2e.finishDmSend(job.peer, job.sendId, current) || !current()) return false;
        await onApplied(job.messageId, current);
        return remove();
    }
    function live(job, ctx) {
        return () => ctx.current() && getPeerPub(job.peer) === job.peerPub
            && JSON.stringify(contextForView(job.view)) === JSON.stringify(job.context);
    }
    async function retry(id) {
        return serialized(id, async ctx => {
            if (!await restore(ctx)) return false;
            let job = jobs.get(id);
            if (!job || job.scope !== ctx.scope || job.blocked) return false;
            const current = live(job, ctx);
            if (!current()) { onState(id, 'key-or-context'); return false; }
            if (job.committed) return complete(job, current, ctx);
            if (!job.payload) {
                // Revalidate the original in this account before spending a
                // key. A crash after encryption returns the journaled header.
                const original = await getMessage(job.messageId);
                if (!current()) return false;
                const ref = nativeMessageRef(original, job.context);
                if (!ref || ref.author !== 'actor' || ref.id !== job.event.file_id
                    || (original.native_edit_seq ?? 0) + 1 !== job.event.native_sequence) {
                    // A retained author/context tombstone does not cancel a
                    // previously validated intent or its journaled header.
                    if (original || !await isDeleted(job.messageId, job.context, current) || !current()) return false;
                }
                const encrypted = await e2e.ratchetEncrypt(job.peer, job.plaintext,
                    { sendId: job.sendId, peerPub: job.peerPub, holdForReceive: true, current });
                if (!current()) return false;
                const payload = header(encrypted), headerDigest = await dmEnvelopeFingerprint(payload);
                const mediaDigest = b64u(await dmEnvelopeFingerprint({ kind: 'native-edit', scope: ctx.scope,
                    id, peer: job.peer, peer_pub: job.peerPub, header_digest: headerDigest }));
                if (!current()) return false;
                job = await save({ ...job, payload, headerDigest, mediaDigest }, ctx);
            }
            if (!current()) return false;
            if (job.headerCommitted && !await complete(job, current, ctx)) return false;
            const sent = await send({ cmd: 'native_edit_send', v: 1, cert_id: job.view.id, message_id: job.id,
                file_id: job.event.file_id, native_sequence: job.event.native_sequence, parent_header_digest: job.event.parent_header_digest,
                recipient_pub: job.peerPub, media_digest: job.mediaDigest, payload: job.payload }, current);
            if (current()) onState(id, job.headerCommitted ? 'history-pending' : 'header-pending');
            return sent !== false && current();
        });
    }
    function enqueue(message, view, text) {
        const ctx = context();
        const run = intents.catch(() => {}).then(async () => {
            if (!ctx.current()) throw Error('Native edit unavailable');
            const route = contextForView(view), pub = getPeerPub(route?.peerWebId);
            const id = 'ne-' + crypto.randomUUID();
            // All local reference/text bounds are checked before preflight and
            // before touching the ratchet. A placeholder digest is never sent.
            const input = { actionId: id, text, sequence: (message?.native_edit_seq ?? 0) + 1, parentHeaderDigest: '0'.repeat(64) };
            const validated = encodeNativeEdit(message, route, input);
            if (!PUB.test(pub || '') || !await restore(ctx) || !ctx.current()) throw Error('Native edit key unavailable');
            const prior = [...jobs.values()].find(job => job.scope === ctx.scope && job.messageId === message.message_id);
            if (prior) {
                if (decodeNativeEdit(prior.plaintext, prior.event)?.text !== validated.body.text) throw Error('Native edit already pending');
                return prior;
            }
            if ([...jobs.values()].filter(job => job.scope === ctx.scope).length >= 10) throw Error('Native edit queue full');
            const current = () => ctx.current() && pub === getPeerPub(route.peerWebId)
                && JSON.stringify(route) === JSON.stringify(contextForView(view));
            const result = await capability({ cert_id: view.id, file_id: validated.body.ref.id,
                peer_webid: route.peerWebId, expected_pub: pub }, current);
            if (!current() || result?.supported !== true || result.peer_pub !== pub
                || result.native_sequence !== input.sequence || !SHA.test(result.parent_header_digest || '')) throw Error('Native edit peer unavailable');
            const encoded = encodeNativeEdit(message, route, { ...input, parentHeaderDigest: result.parent_header_digest });
            const job = { kind: 'native-edit', scope: ctx.scope, id, peer: route.peerWebId, peerPub: pub,
                view: { id: view.id, type: view.type, peerWebid: route.peerWebId }, context: route, messageId: message.message_id,
                plaintext: encoded.plaintext, event: { type: 'native_edit', native_edit_v: 1, from_webid: route.selfWebId,
                    recipient_pub: pub, message_id: id, file_id: encoded.body.ref.id, native_sequence: encoded.body.seq,
                    parent_header_digest: encoded.body.parent }, timestamp: new Date().toISOString(),
                sendId: await dmEnvelopeFingerprint({ kind: 'native-edit', scope: ctx.scope, message_id: id }) };
            if (!current()) throw Error('Native edit context changed');
            return save(job, { ...ctx, current });
        });
        intents = run; return run;
    }
    return { enqueue, retry,
        async status(event) {
            if (event?.type !== 'native_media_status' || event.v !== 1 || event.native_edit_v !== 1
                || !['pending', 'forwarded', 'committed', 'blocked'].includes(event.status)
                || typeof event.header_committed !== 'boolean' || event.status === 'committed' && !event.header_committed) return false;
            return serialized(event.message_id, async ctx => {
                if (!await restore(ctx)) return false;
                let job = jobs.get(event.message_id);
                if (!job?.payload || event.media_digest !== job.mediaDigest || event.header_digest !== job.headerDigest) return false;
                const current = live(job, ctx);
                if (!current()) return false;
                job = await save({ ...job, headerCommitted: !!job.headerCommitted || event.header_committed,
                    committed: !!job.committed || event.status === 'committed', blocked: !!job.blocked || event.status === 'blocked' }, { ...ctx, current });
                if (job.blocked) {
                    // A proven consumed header can release ordering without
                    // cancelling or completing the blocked prepared caption.
                    if (job.headerCommitted) await e2e.releaseDmSendHold(job.peer, job.sendId, current);
                    if (current()) onState(job.id, 'blocked');
                    return false;
                }
                return complete(job, current, { ...ctx, current });
            });
        },
        async recover() {
            const ctx = context();
            if (!ctx.current() || !await restore(ctx)) return false;
            for (const job of [...jobs.values()].filter(job => job.scope === ctx.scope).slice(0, 10)) {
                if (!ctx.current()) return false;
                try { await retry(job.id); } catch (error) { onState(job.id, error?.name === 'E2EHeaderPendingError' ? 'header-pending' : 'storage'); }
            }
            return ctx.current();
        },
        async inspect() {
            const ctx = context(true);
            if (!await restore(ctx)) return [];
            return [...jobs.values()].filter(job => job.scope === ctx.scope).map(job => ({ id: job.id, peer: job.peer, timestamp: job.timestamp,
                state: job.blocked ? 'blocked' : job.headerCommitted ? 'history-pending' : 'header-pending', canCancel: false }));
        },
        stop() { epoch++; jobs.clear(); },
    };
}
