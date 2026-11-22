import { createNativeMediaSender } from './native-media-send.js';
import { createNativeMediaReceiver } from './native-media-receive.js';
import { createNativeMediaStore } from './native-media-drafts.js';
import { createNativeChunkUploader } from './native-media-chunks-send.js';
import { createNativeChunkReceiver } from './native-media-chunks-receive.js';
import { createNativeMediaInbox } from './native-media-inbox.js';
import { NATIVE_FILE_BYTES } from './native-media-protocol.js';

// Socket replies are correlated to the original peer/key and connection epoch.
// File bytes use direct sends, never the persisted generic command queue.
export function createNativeMedia({ secondary, e2e, receipts, getScope, getGeneration = () => 0,
    isActive, send, persistOwnMessage, persistMessage, handleEvent, toMessage, getPeerPub, getOwnPub, persistFile, isPersistedFile,
    inbox = createNativeMediaInbox(),
    store = createNativeMediaStore(globalThis.indexedDB, { maxCipherBytes: NATIVE_FILE_BYTES }), onState = () => {}, doc = globalThis.document,
    onError = err => console.warn('[native-media] pending:', err) }) {
    let version = 0, chunkVersion = 0, declineVersion = 0, epoch = 0, timer = null, recovering = null;
    const pending = new Map(), states = new Map(), progress = new Map();
    const active = () => version === 1 && isActive();
    const chunksConfigured = typeof getOwnPub === 'function' && typeof persistFile === 'function';
    const chunkActive = () => active() && chunkVersion === 1 && chunksConfigured && /^[A-Za-z0-9_-]{43}$/.test(getOwnPub() || '');
    const declineActive = () => chunkActive() && declineVersion === 1;
    const generation = () => JSON.stringify([epoch, getGeneration()]);
    const guardedSend = (command, current = active) => current() && active() ? send(command) : false;
    function capability(request, current) {
        if (!active() || !current() || (request.native_chunks_v === 1 && !chunkActive()) || pending.size >= 10) return Promise.resolve(null);
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
                    cert_id: request.cert_id, expected_pub: request.expected_pub,
                    ...(request.native_chunks_v === 1 ? { native_chunks_v: 1 } : {}) }, current) === false) finish(null);
            } catch (err) { finish(null); onError(err); }
        });
    }
    const sender = createNativeMediaSender({ secondary, e2e, store, getScope, getGeneration: generation,
        isActive: active, getPeerPub, capability, send: guardedSend, persistOwnMessage, onError,
        chunkUpload: (...args) => uploader.start(...args), chunkUploadEnabled: chunkActive,
        onState: (id, state) => { if (state === 'committed') states.delete(id); else states.set(id, state); onState(id, state); } });
    const receiver = createNativeMediaReceiver({ e2e, receipts, getScope, getGeneration: generation,
        isActive: active, persistMessage, isPersistedFile, handleEvent, toMessage, sendCommit: guardedSend, onError });
    const uploader = createNativeChunkUploader({ send: guardedSend, getScope, getGeneration: generation,
        isActive: chunkActive, getPeerPub, onProgress: (event, current) => current() ? sender.status(event) : false,
        onState: (id, state, detail) => { states.set(id, state); if (detail) progress.set(id, detail); else progress.delete(id); onState(id, state); } });
    const chunks = createNativeChunkReceiver({ receiver, inbox, getScope, getGeneration: generation,
        isActive: chunkActive, getOwnPub, send: guardedSend, persistFile, onError });
    async function recover() {
        if (!active()) return false;
        if (recovering) return recovering;
        const stamp = generation();
        const run = (async () => {
            await receiver.recover();
            if (!active() || stamp !== generation()) return false;
            if (chunkActive()) await chunks.recover();
            if (!active() || stamp !== generation()) return false;
            await sender.recover(); return true;
        })();
        recovering = run;
        try { return await run; }
        finally { if (recovering === run) recovering = null; }
    }
    function stop() {
        epoch++; version = 0; chunkVersion = 0; declineVersion = 0;
        if (timer) clearInterval(timer); timer = null;
        for (const row of [...pending.values()]) row.finish(null);
        sender.stop(); receiver.stop(); uploader.stop(); chunks.stop(); states.clear(); progress.clear(); recovering = null;
        doc?.removeEventListener('visibilitychange', onVisible);
    }
    const onVisible = () => { if (!doc.hidden && active()) recover().catch(onError); };
    return { available: active, chunksAvailable: chunkActive, sender, receiver, recover, stop,
        start(negotiatedVersion, negotiatedChunks, negotiatedDecline) {
            stop(); version = negotiatedVersion === 1 ? 1 : 0; chunkVersion = negotiatedChunks === 1 ? 1 : 0; declineVersion = negotiatedDecline === 1 ? 1 : 0;
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
                    && event.peer_webid === row.request.peer_webid && event.expected_pub === row.request.expected_pub
                    && (row.request.native_chunks_v !== 1 || event.native_chunks_v === 1)) {
                    row.finish(event.supported && event.peer_pub === row.request.expected_pub ? event : null);
                }
                return true;
            }
            if (chunkActive() && event?.type === 'native_media_parts') { await uploader.status(event); return true; }
            if (chunkActive() && event?.type === 'native_media_offer') { await chunks.offer(event); return true; }
            if (chunkActive() && event?.type === 'native_media_chunk') { await chunks.part(event); return true; }
            if (event?.type === 'native_media_status') { await sender.status(event); return true; }
            if (['native_media_declined', 'native_media_decline_refused'].includes(event?.type)) { await receiver.confirmDecline(event); return true; }
            if (event?.type === 'native_media_committed') { await receiver.confirmCommit(event); return true; }
            if (event?.type === 'native_media_header_committed') return true;
            if (event?.type === 'message' && event.native_media_v === 1) { await receiver.receive(event); return true; }
            return false;
        },
        async inspect() {
            // Legacy, linked and pod connections must not disable their normal
            // outbox just because native media is not negotiated. Read retained
            // primary drafts locally even while that protocol is disconnected.
            if (!isActive()) return [];
            const scope = getScope(), stamp = generation();
            const current = () => isActive() && scope === getScope() && stamp === generation();
            let rows = [], receiving = [], incoming = [], unavailable = false;
            try { rows = await store.list(scope, current); }
            catch (err) { if (!current()) return []; unavailable = true; onError(err); }
            try { receiving = await e2e.pendingDmReceives({ transport: 'native-media' }); }
            catch (err) { if (!current()) return []; unavailable = true; onError(err); }
            if (chunksConfigured) {
                try { incoming = await inbox.list(scope, current); }
                catch (err) { if (!current()) return []; unavailable = true; onError(err); }
            }
            if (!current()) return [];
            return [...rows.map(row => ({ id: 'native:' + row.id, peer: row.peer, timestamp: row.timestamp,
                state: !active() ? 'nativeConnection' : row.committed ? 'nativeHistory' : row.blocked ? 'nativeBlocked' : row.declined ? 'nativeDeclined' : row.peerPub && getPeerPub(row.peer) !== row.peerPub ? 'nativeKey'
                    : states.get(row.id) === 'uploading' ? 'nativeUpload' : row.headerCommitted ? 'nativeFile' : row.stage === 'sealed' || states.get(row.id) === 'header-pending' ? 'nativeHeader'
                        : states.get(row.id) === 'peer-unavailable' ? 'nativePeer' : 'nativeWaiting',
                canCancel: row.stage === 'source', canRetry: active() && !row.blocked,
                ...(states.get(row.id) === 'uploading' && progress.has(row.id) ? { progress: progress.get(row.id) } : {}) })),
                ...incoming.map(row => ({ id: 'native-in:chunk:' + row.key, peer: row.sender, timestamp: row.timestamp,
                    canCancel: false, canRetry: chunkActive(), canDecline: declineActive() && row.manifest.recipient_pub === getOwnPub(), state: chunkActive() ? 'nativeIncoming' : 'nativeConnection',
                    progress: { received: row.received, total: row.manifest.chunk_count } })),
                ...receiving.filter(row => row.data.scope === scope
                    && !incoming.some(file => file.sender === row.peerId && file.id === row.data.commit?.message_id)).map(row => ({ id: 'native-in:' + row.receiveId,
                    peer: row.peerId, timestamp: row.data.timestamp, canCancel: false, canRetry: active(),
                    state: active() ? 'nativeIncoming' : 'nativeConnection' })),
                ...(unavailable ? [{ id: 'native-in:storage', peer: '', state: 'nativeStorage', canCancel: false, canRetry: true }] : [])];
        },
        async decline(id) {
            if (!declineActive()) return false;
            const scope = getScope(), stamp = generation();
            const current = () => declineActive() && scope === getScope() && stamp === generation();
            const row = (await inbox.list(scope, current)).find(row => 'native-in:chunk:' + row.key === id);
            return row && current() ? chunks.decline(row.sender, row.id) : false;
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
