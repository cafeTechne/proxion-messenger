import { dmEnvelopeFingerprint } from './dm-receipts.js';
import { createNativeMediaPreparation } from './native-media-drafts.js';

function b64(bytes) {
    let text = '';
    for (const byte of bytes) text += String.fromCharCode(byte);
    return btoa(text);
}
const hexBytes = hex => Uint8Array.from(hex.match(/../g), value => parseInt(value, 16));
const headerKeys = ['e2e', 'e2e_v', 'content', 'nonce', 'msg_num', 'pn', 'ratchet_pub'];

export async function nativeMediaSendIdentity(row) {
    const header = Object.fromEntries(headerKeys.map(key => [key, row.hdr[key]]));
    const headerDigest = await dmEnvelopeFingerprint(header);
    const ctDigest = b64(new Uint8Array(await crypto.subtle.digest('SHA-256', row.prepared.ct)));
    const digest = await dmEnvelopeFingerprint({ scope: row.scope, id: row.id, peer: row.peer,
        peer_pub: row.peerPub, header_digest: headerDigest, ciphertext_digest: ctDigest });
    const sendId = await dmEnvelopeFingerprint({ scope: row.scope, message_id: row.id, kind: 'native-inline-file' });
    return { headerDigest, mediaDigest: b64(hexBytes(digest)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''), sendId };
}

// The authenticated socket adapter supplies capability and status results.
// Nothing here interprets a socket write or gateway custody as consumption.
export function createNativeMediaSender({ secondary, e2e, store, getScope, getGeneration = () => 0,
    isActive = () => true, getPeerPub, capability, send, persistOwnMessage, onState = () => {},
    onError = err => console.warn('[native-media] send pending:', err) }) {
    let epoch = 0;
    const queues = new Map();
    const preparation = createNativeMediaPreparation({ secondary, store, getScope,
        getGeneration: () => JSON.stringify([epoch, getGeneration()]),
        isActive, getPeerPub, holdForReceive: true });
    function context() {
        const scope = getScope(), generation = getGeneration(), stamp = epoch;
        return { scope, current: () => scope === getScope() && generation === getGeneration() && stamp === epoch && isActive() };
    }
    function serialize(id, work) {
        const ctx = context(), key = JSON.stringify([ctx.scope, id]);
        if (!queues.has(key) && queues.size >= 10) return Promise.resolve(false);
        const run = (queues.get(key) || Promise.resolve()).catch(() => {}).then(() => work(ctx));
        queues.set(key, run);
        run.finally(() => { if (queues.get(key) === run) queues.delete(key); }).catch(() => {});
        return run;
    }
    async function complete(row, identity, current) {
        if (!current()) return false;
        if (!row.headerCommitted) return true;
        const remove = async () => {
            await store.removeCompleted(row.scope, row.id, row.revision, current);
            if (current()) onState(row.id, 'committed');
            return true;
        };
        // Source deletion may fail after the ratchet cleanup was committed.
        // With saved full progress/history, an already-finished send is enough
        // to retry deletion; its now-absent hold cannot be released again.
        if (row.committed && row.historySaved
            && await e2e.finishDmSend(row.peer, identity.sendId, current)) {
            if (!current()) return false;
            return remove();
        }
        if (!await e2e.releaseDmSendHold(row.peer, identity.sendId, current) || !current()) return false;
        if (!row.committed) return true;
        if (!row.historySaved) {
            if (await persistOwnMessage(row, current) === false || !current()) return false;
            row = await store.progress(row.scope, row.id, row.revision, { historySaved: true }, current);
        }
        if (!current() || !await e2e.finishDmSend(row.peer, identity.sendId, current) || !current()) return false;
        return remove();
    }
    function retry(id) {
        return serialize(id, async ctx => {
            if (!ctx.current()) return false;
            let row = await store.get(ctx.scope, id, ctx.current);
            if (!row || row.receiveHold !== true || !ctx.current()) return false;
            if (row.blocked) { onState(id, 'blocked'); return false; }
            const peerPub = getPeerPub(row.peer);
            if (!peerPub || (row.peerPub && row.peerPub !== peerPub)) { onState(id, 'key-unavailable'); return false; }
            const current = () => ctx.current() && getPeerPub(row.peer) === peerPub;
            if (row.stage !== 'sealed') {
                // A negative or lost preflight leaves the original source and
                // ratchet position untouched. Never downgrade a prepared draft.
                const result = await capability({ cert_id: row.view.id, peer_webid: row.peer, expected_pub: peerPub }, current);
                if (!current()) return false;
                if (result?.supported !== true || result.peer_pub !== peerPub) { onState(id, 'peer-unavailable'); return false; }
                row = await preparation.prepare(id);
            }
            if (!current()) return false;
            const identity = await nativeMediaSendIdentity(row);
            if (!current()) return false;
            if (row.committed) return complete(row, identity, current);
            if (row.headerCommitted && !await complete(row, identity, current)) return false;
            const result = await send({ cmd: 'send_file', cert_id: row.view.id, message_id: id,
                native_media_v: 1, media_digest: identity.mediaDigest, ...row.hdr,
                filename: 'file', mime_type: 'application/octet-stream',
                data_b64: b64(row.prepared.ct) }, current);
            if (current()) onState(id, row.headerCommitted ? 'file-pending' : 'header-pending');
            return result !== false && current();
        });
    }
    function status(event) {
        if (event?.type !== 'native_media_status' || event.v !== 1
            || !/^[A-Za-z0-9_-]{1,128}$/.test(event.message_id || '')
            || !['pending', 'forwarded', 'committed', 'blocked'].includes(event.status)
            || typeof event.header_committed !== 'boolean'
            || (event.status === 'committed' && !event.header_committed)) return Promise.resolve(false);
        return serialize(event.message_id, async ctx => {
            if (!ctx.current()) return false;
            let row = await store.get(ctx.scope, event.message_id, ctx.current);
            if (!row || row.stage !== 'sealed' || row.receiveHold !== true || getPeerPub(row.peer) !== row.peerPub) return false;
            const current = () => ctx.current() && getPeerPub(row.peer) === row.peerPub;
            const identity = await nativeMediaSendIdentity(row);
            if (!current() || event.media_digest !== identity.mediaDigest || event.header_digest !== identity.headerDigest) return false;
            const patch = { ...(event.header_committed && !row.headerCommitted ? { headerCommitted: true } : {}),
                ...(event.status === 'committed' && !row.committed ? { committed: true } : {}),
                ...(event.status === 'blocked' && !row.blocked ? { blocked: true } : {}) };
            if (Object.keys(patch).length) row = await store.progress(ctx.scope, row.id, row.revision, patch, current);
            if (!current()) return false;
            const completed = await complete(row, identity, current);
            if (current() && !row.committed) onState(row.id, row.blocked ? 'blocked' : row.headerCommitted ? 'file-pending' : 'header-pending');
            return completed;
        });
    }
    return { enqueue: preparation.enqueue, list: preparation.list, retry, status,
        async recover() {
            const ctx = context();
            if (!ctx.current()) return false;
            const rows = await store.list(ctx.scope, ctx.current);
            if (!ctx.current()) return false;
            const ids = rows.filter(row => row.stage === 'sealed').map(row => row.id);
            await send({ cmd: 'native_media_recover', message_ids: ids }, ctx.current);
            for (const row of rows) {
                if (!ctx.current()) return false;
                try { await retry(row.id); }
                catch (err) {
                    if (!ctx.current()) return false;
                    onState(row.id, err?.name === 'E2EHeaderPendingError' ? 'header-pending' : 'storage'); onError(err);
                }
            }
            return true;
        },
        stop() { epoch++; },
    };
}
