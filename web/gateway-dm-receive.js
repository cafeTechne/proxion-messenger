import { dmEnvelopeFingerprint } from './dm-receipts.js';

// Only incoming encrypted text and addressed device fanout enter this reader.
export function isGatewayDmReceive(event, selfId, deviceId) {
    if (!event?.from_webid || !event.message_id) return false;
    if (event.type === 'dm_fanout') return event.to_device_id === deviceId && !!(event.payload?.e2e && event.payload.nonce);
    return event.type === 'message' && !event.room_id && !event.file && event.e2e && !!event.nonce
        && event.from_webid !== selfId && event.from_webid !== deviceId;
}

// Ignore server clock/route annotations, but bind every cryptographic field and
// message identity. A changed ciphertext must never match a prior receipt.
export function gatewayDmEnvelope(event) {
    const p = event.type === 'dm_fanout' ? event.payload : event;
    return {
        type: event.type, from_webid: event.from_webid, message_id: event.message_id,
        to_device_id: event.to_device_id || '',
        ...(event.type === 'dm_fanout' ? { payload: p } : {
            content: p.content, nonce: p.nonce, msg_num: p.msg_num ?? 0,
            ratchet_pub: p.ratchet_pub ?? null, pn: p.pn ?? 0,
            reply_to_id: p.reply_to_id || null, x25519_pub: p.x25519_pub || null,
        }),
    };
}

export function createGatewayDmReceiver({ e2e, receipts, persistMessage, handleEvent,
    getScope, getGeneration = () => 0, toMessage, isActive = () => true, isSealedPlain = () => false,
    onError = err => console.warn('[gateway-dm] receive pending:', err) }) {
    let generation = 0, timer = null, recovering = null;
    const queues = new Map(), presented = new Set(), waiting = new Map();
    const context = () => {
        const scope = getScope(), epoch = generation, session = getGeneration();
        return { scope, current: () => epoch === generation && scope === getScope() && session === getGeneration() && isActive() };
    };
    function serialized(key, work) {
        const run = (queues.get(key) || Promise.resolve()).catch(() => {}).then(work);
        queues.set(key, run);
        run.finally(() => { if (queues.get(key) === run) queues.delete(key); }).catch(() => {});
        return run;
    }
    async function complete(row, ctx) {
        const key = ctx.scope + row.receiveId;
        if (!ctx.current()) return false;
        if (await receipts.has(ctx.scope, row.receiveId)) {
            if (!ctx.current()) return false;
            await receipts.acknowledge(ctx.scope, row.receiveId);
            if (!ctx.current()) return false;
            await e2e.finishDmReceive(row.peerId, row.receiveId, ctx.current);
            presented.delete(key);
            return true;
        }
        if (!ctx.current()) return false;
        const message = { ...row.data.message, content: row.content, e2e: false,
            _persistDm: true, _dmHistoryManaged: true };
        if (!isSealedPlain(message.content)) {
            if (!presented.has(key)) {
                presented.add(key);
                try { await handleEvent(message); } catch (err) { onError(err); }
            }
            if (!ctx.current() || await persistMessage(message) === false || !ctx.current()) return false;
        }
        if (!await receipts.remember(ctx.scope, row.receiveId) || !ctx.current()) return false;
        await receipts.acknowledge(ctx.scope, row.receiveId);
        if (!ctx.current()) return false;
        await e2e.finishDmReceive(row.peerId, row.receiveId, ctx.current);
        presented.delete(key);
        return true;
    }
    async function receive(event) {
        const ctx = context();
        if (!ctx.current()) return false;
        const receiveId = await dmEnvelopeFingerprint(gatewayDmEnvelope(event));
        if (!ctx.current()) return false;
        const key = ctx.scope + receiveId;
        if (!waiting.has(key) && waiting.size >= 1024) return false;
        waiting.set(key, { scope: ctx.scope, event });
        const result = await serialized(key, async () => {
            if (!ctx.current()) return false;
            const p = event.type === 'dm_fanout' ? event.payload : event;
            const peerId = event.type === 'dm_fanout'
                ? event.from_webid + '#' + (p.from_device_id || event.from_webid) : event.from_webid;
            if (await receipts.has(ctx.scope, receiveId)) {
                if (!ctx.current()) return false;
                await receipts.acknowledge(ctx.scope, receiveId);
                if (!ctx.current()) return false;
                await e2e.finishDmReceive(peerId, receiveId, ctx.current);
                return true;
            }
            if (!ctx.current() || await receipts.hasSpace(ctx.scope) === false) return false;
            if (!presented.has(ctx.scope + receiveId) && presented.size >= 1024) return false;
            const message = toMessage(event);
            if (!message?.message_id || !message.thread_id) return false;
            const data = { transport: 'gateway', scope: ctx.scope, message };
            let content;
            try {
                content = await e2e.ratchetDecrypt(peerId, p.content, p.nonce, p.msg_num ?? 0,
                    p.ratchet_pub ?? null, p.pn ?? 0, { receiveId, receiveData: data, current: ctx.current });
            } catch (err) {
                // Invalid/spent ciphertext cannot recover by retrying local
                // storage. Keep transient storage failures, not poison events.
                if (err?.name === 'E2EDecryptError' || err?.name === 'E2ENoSessionError') waiting.delete(key);
                throw err;
            }
            return complete({ peerId, receiveId, content, data }, ctx);
        });
        if (result) waiting.delete(key);
        return result;
    }
    function recover() {
        if (recovering) return recovering;
        const ctx = context();
        const run = (async () => {
            if (!ctx.current()) return;
            for (const row of await e2e.pendingDmReceives()) {
                if (!ctx.current()) return;
                if (row.data.scope !== ctx.scope) continue;
                try { await serialized(ctx.scope + row.receiveId, () => complete(row, ctx)); }
                catch (err) { onError(err); }
            }
            for (const row of [...waiting.values()]) {
                if (!ctx.current()) return;
                if (row.scope !== ctx.scope) continue;
                try { await receive(row.event); } catch (err) { onError(err); }
            }
        })();
        recovering = run;
        run.finally(() => { if (recovering === run) recovering = null; }).catch(() => {});
        return run;
    }
    return {
        receive, recover,
        start() {
            if (!timer) timer = setInterval(() => recover().catch(onError), 5000);
            return recover().catch(onError);
        },
        stop() { generation++; if (timer) clearInterval(timer); timer = null; presented.clear(); waiting.clear(); recovering = null; },
    };
}
