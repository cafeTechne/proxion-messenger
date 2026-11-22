import { createNativeMediaSender } from './native-media-send.js';
import { createNativeMediaReceiver } from './native-media-receive.js';
import { createNativeMediaStore } from './native-media-drafts.js';

// Socket replies are correlated to the original peer/key and connection epoch.
// File bytes use direct sends, never the persisted generic command queue.
export function createNativeMedia({ secondary, e2e, receipts, getScope, getGeneration = () => 0,
    isActive, send, persistOwnMessage, persistMessage, handleEvent, toMessage, getPeerPub,
    store = createNativeMediaStore(), onState = () => {}, doc = globalThis.document,
    onError = err => console.warn('[native-media] pending:', err) }) {
    let version = 0, epoch = 0, timer = null, recovering = null;
    const pending = new Map(), states = new Map();
    const active = () => version === 1 && isActive();
    const generation = () => JSON.stringify([epoch, getGeneration()]);
    const guardedSend = (command, current = active) => current() && active() ? send(command) : false;
    function capability(request, current) {
        if (!active() || !current() || pending.size >= 10) return Promise.resolve(null);
        return new Promise(resolve => {
            const requestId = crypto.randomUUID();
            const finish = result => {
                const row = pending.get(requestId);
                if (!row) return;
                clearTimeout(row.timeout); pending.delete(requestId); resolve(result);
            };
            const timeout = setTimeout(() => finish(null), 6000);
            pending.set(requestId, { request, current, finish, timeout });
            try {
                if (guardedSend({ cmd: 'native_media_capability', request_id: requestId,
                    cert_id: request.cert_id, expected_pub: request.expected_pub }, current) === false) finish(null);
            } catch (err) { finish(null); onError(err); }
        });
    }
    const sender = createNativeMediaSender({ secondary, e2e, store, getScope, getGeneration: generation,
        isActive: active, getPeerPub, capability, send: guardedSend, persistOwnMessage, onError,
        onState: (id, state) => { if (state === 'committed') states.delete(id); else states.set(id, state); onState(id, state); } });
    const receiver = createNativeMediaReceiver({ e2e, receipts, getScope, getGeneration: generation,
        isActive: active, persistMessage, handleEvent, toMessage, sendCommit: guardedSend, onError });
    async function recover() {
        if (!active()) return false;
        if (recovering) return recovering;
        const stamp = generation();
        const run = (async () => {
            await receiver.recover();
            if (!active() || stamp !== generation()) return false;
            await sender.recover(); return true;
        })();
        recovering = run;
        try { return await run; }
        finally { if (recovering === run) recovering = null; }
    }
    function stop() {
        epoch++; version = 0;
        if (timer) clearInterval(timer); timer = null;
        for (const row of [...pending.values()]) row.finish(null);
        sender.stop(); receiver.stop(); states.clear(); recovering = null;
        doc?.removeEventListener('visibilitychange', onVisible);
    }
    const onVisible = () => { if (!doc.hidden && active()) recover().catch(onError); };
    return { available: active, sender, receiver, recover, stop,
        start(negotiatedVersion) {
            stop(); version = negotiatedVersion === 1 ? 1 : 0;
            if (!active()) return;
            doc?.addEventListener('visibilitychange', onVisible);
            timer = setInterval(() => recover().catch(onError), 5000);
            recover().catch(onError);
        },
        async handleEvent(event) {
            if (!active()) return false;
            if (event?.type === 'native_media_capability_result') {
                const row = pending.get(event.request_id);
                if (row?.current() && event.v === 1 && typeof event.supported === 'boolean'
                    && event.peer_webid === row.request.peer_webid && event.expected_pub === row.request.expected_pub) {
                    row.finish(event.supported && event.peer_pub === row.request.expected_pub ? event : null);
                }
                return true;
            }
            if (event?.type === 'native_media_status') { await sender.status(event); return true; }
            if (event?.type === 'native_media_committed') { await receiver.confirmCommit(event); return true; }
            if (event?.type === 'native_media_header_committed') return true;
            if (event?.type === 'message' && event.native_media_v === 1) { await receiver.receive(event); return true; }
            return false;
        },
        async inspect() {
            const rows = await sender.list();
            return rows.map(row => ({ id: 'native:' + row.id, peer: row.peer, timestamp: row.timestamp,
                state: row.committed ? 'nativeHistory' : row.blocked ? 'nativeBlocked' : row.peerPub && getPeerPub(row.peer) !== row.peerPub ? 'nativeKey'
                    : row.headerCommitted ? 'nativeFile' : row.stage === 'sealed' || states.get(row.id) === 'header-pending' ? 'nativeHeader'
                        : states.get(row.id) === 'peer-unavailable' ? 'nativePeer' : 'nativeWaiting',
                canCancel: row.stage === 'source', canRetry: !row.blocked }));
        },
        async cancel(id) {
            if (!isActive()) return false;
            const scope = getScope(), stamp = generation();
            const current = () => isActive() && scope === getScope() && stamp === generation();
            const row = await store.get(scope, id, current);
            const cancelled = row ? await store.cancelSource(scope, id, row.revision, current) : false;
            if (cancelled) states.delete(id);
            return cancelled;
        },
    };
}
