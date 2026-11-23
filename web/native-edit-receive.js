import { dmEnvelopeFingerprint } from './dm-receipts.js';
import { decodeNativeEdit } from './native-edit-codec.js';

const ID = /^[A-Za-z0-9_-]{1,128}$/;
const DID = /^did:key:[A-Za-z0-9_-]{1,2040}$/;
const SHA = /^[a-f0-9]{64}$/;
const PUB = /^[A-Za-z0-9_-]{43}$/;
const HEADER = ['e2e', 'e2e_v', 'content', 'nonce', 'msg_num', 'pn', 'ratchet_pub'];

export async function nativeEditReceiveIdentity(event) {
    const token = event?.native_commit;
    if (event?.type !== 'native_edit' || event.native_edit_v !== 1 || event.room_id
        || typeof event.from_webid !== 'string' || !DID.test(event.from_webid)
        || typeof event.message_id !== 'string' || !ID.test(event.message_id)
        || typeof event.file_id !== 'string' || !ID.test(event.file_id) || event.message_id === event.file_id
        || !Number.isSafeInteger(event.native_sequence) || event.native_sequence < 1 || event.native_sequence > 100
        || typeof event.parent_header_digest !== 'string' || !SHA.test(event.parent_header_digest)
        || typeof event.recipient_pub !== 'string' || !PUB.test(event.recipient_pub)
        || token?.v !== 1 || !Number.isSafeInteger(token.queue_id) || token.queue_id < 1
        || typeof token.envelope_digest !== 'string' || !SHA.test(token.envelope_digest)
        || event.e2e !== true || event.e2e_v !== 1 || typeof event.content !== 'string'
        || !/^[A-Za-z0-9_-]{1,16384}$/.test(event.content)
        || typeof event.nonce !== 'string' || !/^[A-Za-z0-9_-]{16}$/.test(event.nonce)
        || typeof event.ratchet_pub !== 'string' || !PUB.test(event.ratchet_pub)
        || !Number.isSafeInteger(event.msg_num) || event.msg_num < 0
        || !Number.isSafeInteger(event.pn) || event.pn < 0) throw Error('Invalid native caption envelope');
    const saved = Object.fromEntries([...HEADER, 'type', 'native_edit_v', 'from_webid', 'message_id', 'file_id',
        'native_sequence', 'parent_header_digest', 'recipient_pub'].map(key => [key, event[key]]));
    const receiveId = await dmEnvelopeFingerprint({ transport: 'native-edit', ...saved });
    const commit = { cmd: 'native_media_commit', v: 1, native_edit_v: 1, queue_id: token.queue_id,
        from_webid: event.from_webid, message_id: event.message_id, envelope_digest: token.envelope_digest, receive_id: receiveId };
    return { receiveId, commit, event: { ...saved, native_commit: { v: 1, queue_id: token.queue_id, envelope_digest: token.envelope_digest } } };
}

// Only authenticated gateway events enter this adapter. The caller supplies
// the current peer/thread mapping; a wire thread string cannot choose a local
// conversation. Caption + action identity must commit before a full receipt.
export function createNativeEditReceiver({ e2e, receipts, getScope, getGeneration = () => 0, isActive = () => true,
    getOwnPub, contextForPeer, applyEdit, onApplied = () => {}, send, onError = () => {} }) {
    let epoch = 0;
    const queues = new Map(), offsets = { receive: 0, commits: 0 };
    const context = () => {
        const scope = getScope(), generation = getGeneration(), stamp = epoch, pub = getOwnPub();
        return { scope, receiptScope: 'native-edit:' + scope,
            current: () => isActive() && stamp === epoch && generation === getGeneration() && scope === getScope() && pub === getOwnPub() };
    };
    const batch = (rows, kind) => {
        if (!rows.length) { offsets[kind] = 0; return []; }
        const start = offsets[kind] % rows.length, count = Math.min(4, rows.length);
        offsets[kind] = (start + count) % rows.length;
        return Array.from({ length: count }, (_, index) => rows[(start + index) % rows.length]);
    };
    async function serialized(ctx, id, work) {
        const key = JSON.stringify([ctx.scope, id]);
        if (!queues.has(key) && queues.size >= 10) return false;
        const run = (queues.get(key) || Promise.resolve()).catch(() => {}).then(() => ctx.current() ? work() : false);
        queues.set(key, run);
        try { return await run; }
        catch (error) { onError(error); return false; }
        finally { if (queues.get(key) === run) queues.delete(key); }
    }
    async function consume(identity, ctx, plaintext) {
        const event = identity.event, peer = contextForPeer(event.from_webid);
        if (!ctx.current() || event.recipient_pub !== getOwnPub() || !peer
            || peer.actorWebId !== event.from_webid || peer.peerWebId !== event.from_webid) return false;
        const route = JSON.stringify(peer);
        const current = () => ctx.current() && route === JSON.stringify(contextForPeer(event.from_webid));
        const present = async () => {
            if (!current() || !await e2e.finishDmReceive(event.from_webid, identity.receiveId, current) || !current()) return false;
            await onApplied(`native:${event.from_webid}:${event.file_id}`, current);
            return current();
        };
        if (await receipts.has(ctx.receiptScope, identity.receiveId)) {
            if (!current() || !await receipts.remember(ctx.receiptScope, identity.receiveId, null, identity.commit) || !current()
                || await send(identity.commit, current) === false || !current()) return false;
            return present();
        }
        if (!current() || !await receipts.hasSpace(ctx.receiptScope) || !current()) return false;
        if (plaintext === undefined) {
            plaintext = await e2e.ratchetDecrypt(event.from_webid, event.content, event.nonce, event.msg_num, event.ratchet_pub, event.pn,
                { receiveId: identity.receiveId, receiveData: { transport: 'native-edit', scope: ctx.scope, event }, current, allowInit: false });
        }
        if (!current() || !decodeNativeEdit(plaintext, event)) return false;
        if (await send({ ...identity.commit, cmd: 'native_media_header_commit' }, current) === false || !current()) return false;
        if (!await applyEdit(plaintext, event, peer, { current }) || !current()) return false;
        if (!await receipts.remember(ctx.receiptScope, identity.receiveId, null, identity.commit) || !current()) return false;
        if (await send(identity.commit, current) === false || !current()) return false;
        return present();
    }
    return {
        async receive(event) {
            const ctx = context();
            if (!ctx.current()) return false;
            try {
                const identity = await nativeEditReceiveIdentity(event);
                return ctx.current() ? serialized(ctx, identity.receiveId, () => consume(identity, ctx)) : false;
            } catch (error) { onError(error); return false; }
        },
        async recover() {
            const ctx = context();
            if (!ctx.current()) return false;
            const pending = await e2e.pendingDmReceives({ transport: 'native-edit' });
            for (const row of batch(pending.filter(row => row.data?.scope === ctx.scope), 'receive')) {
                if (!ctx.current()) return false;
                try {
                    const identity = await nativeEditReceiveIdentity(row.data.event);
                    if (identity.receiveId !== row.receiveId || identity.event.from_webid !== row.peerId) continue;
                    await serialized(ctx, identity.receiveId, () => consume(identity, ctx, row.content));
                } catch (error) { onError(error); }
            }
            if (!ctx.current()) return false;
            for (const commit of batch(await receipts.pendingCommits(ctx.receiptScope), 'commits')) {
                if (!ctx.current()) return false;
                await send(commit, ctx.current);
            }
            return ctx.current();
        },
        async confirm(event) {
            const ctx = context();
            if (!ctx.current() || event?.type !== 'native_media_committed' || event.native_edit_v !== 1
                || typeof event.receive_id !== 'string' || !SHA.test(event.receive_id)) return false;
            return receipts.confirmCommit(ctx.receiptScope, event.receive_id, event);
        },
        stop() { epoch++; },
    };
}
