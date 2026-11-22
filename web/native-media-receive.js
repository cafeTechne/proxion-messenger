import { dmEnvelopeFingerprint } from './dm-receipts.js';
import { checkFileBody, decodeSealedPlain, KIND_FILE, openFileBytes } from './e2e-secondary.js';
import { NATIVE_INLINE_BYTES } from './native-media-drafts.js';

const SEALED_KEYS = ['e2e', 'e2e_v', 'content', 'nonce', 'msg_num', 'pn', 'ratchet_pub'];
const hex = bytes => [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
const b64 = bytes => {
    let text = '';
    for (const byte of bytes) text += String.fromCharCode(byte);
    return btoa(text);
};

// Hash ciphertext separately: the encrypted ratchet recovery record must never
// contain an attachment's bytes or its large base64 socket representation.
export async function nativeMediaEnvelope(event) {
    const token = event?.native_commit, file = event?.file;
    if (event?.native_media_v !== 1 || event.type !== 'message' || event.room_id
        || !event.from_webid?.startsWith('did:key:') || event.from_webid.length > 2048
        || !/^[A-Za-z0-9_-]{1,128}$/.test(event.message_id || '')
        || token?.v !== 1 || !Number.isSafeInteger(token.queue_id) || token.queue_id < 1
        || !/^[a-f0-9]{64}$/.test(token.envelope_digest || '')
        || event.e2e !== true || event.e2e_v !== 1 || typeof event.content !== 'string'
        || event.content.length > 4096 || typeof event.nonce !== 'string'
        || typeof event.ratchet_pub !== 'string' || !Number.isSafeInteger(event.msg_num) || event.msg_num < 0
        || !Number.isSafeInteger(event.pn) || event.pn < 0
        || file?.filename !== 'file' || file.mime_type !== 'application/octet-stream'
        || !Number.isSafeInteger(file.size) || file.size < 17 || file.size > NATIVE_INLINE_BYTES
        || typeof file.data_b64 !== 'string' || file.data_b64.length > 699052) throw Error('Invalid native media envelope');
    const bytes = Uint8Array.from(atob(file.data_b64), ch => ch.charCodeAt(0));
    if (bytes.length !== file.size || b64(bytes) !== file.data_b64) throw Error('Invalid native media ciphertext');
    const header = Object.fromEntries(SEALED_KEYS.map(key => [key, event[key]]));
    const headerId = await dmEnvelopeFingerprint({ transport: 'native-media-header',
        from_webid: event.from_webid, message_id: event.message_id, header });
    const ciphertextDigest = hex(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)));
    const receiveId = await dmEnvelopeFingerprint({ transport: 'native-media', from_webid: event.from_webid,
        message_id: event.message_id, header, ciphertext_digest: ciphertextDigest });
    const commit = { cmd: 'native_media_commit', v: 1, queue_id: token.queue_id,
        from_webid: event.from_webid, message_id: event.message_id,
        envelope_digest: token.envelope_digest, receive_id: receiveId };
    return { bytes, receiveId, headerId, commit };
}

// Header consumption and complete file/history persistence have separate
// acknowledgements; a protected full receipt survives a lost socket reply.
export function createNativeMediaReceiver({ e2e, receipts, getScope, getGeneration = () => 0,
    isActive = () => true, persistMessage, handleEvent, toMessage, sendCommit,
    requestReplay = () => {}, onError = err => console.warn('[native-media] receive pending:', err) }) {
    let generation = 0;
    const queues = new Map();
    const context = () => {
        const scope = getScope(), epoch = generation, session = getGeneration();
        return { scope, receiptScope: 'native-media:' + scope,
            current: () => typeof scope === 'string' && scope.length > 0 && scope.length <= 8192
                && epoch === generation && scope === getScope() && session === getGeneration() && isActive() };
    };
    const headerCommit = commit => ({ ...commit, cmd: 'native_media_header_commit' });
    async function receive(event) {
        const ctx = context();
        if (!ctx.current()) return false;
        const envelope = await nativeMediaEnvelope(event);
        if (!ctx.current()) return false;
        const { bytes, receiveId, headerId, commit } = envelope, key = JSON.stringify([ctx.scope, headerId]);
        if (!queues.has(key) && queues.size >= 10) return false;
        const run = (queues.get(key) || Promise.resolve()).catch(() => {}).then(async () => {
            if (!ctx.current()) return false;
            if (await receipts.has(ctx.receiptScope, receiveId)) {
                if (!ctx.current()) return false;
                if (await sendCommit(commit) === false || !ctx.current()) return false;
                return e2e.finishDmReceive(event.from_webid, headerId, ctx.current);
            }
            if (!ctx.current() || !await receipts.hasSpace(ctx.receiptScope) || !ctx.current()) return false;
            const message = toMessage(event);
            if (!message?.message_id || !message.thread_id) return false;
            // Construct the small metadata explicitly; never persist a spread
            // of the socket event or a caller-supplied message containing bytes.
            const data = { transport: 'native-media', scope: ctx.scope, commit };
            if (typeof event.timestamp === 'string' && event.timestamp.length <= 64) data.timestamp = event.timestamp;
            const content = await e2e.ratchetDecrypt(event.from_webid, event.content, event.nonce,
                event.msg_num, event.ratchet_pub, event.pn, { receiveId: headerId, receiveData: data, current: ctx.current });
            if (!ctx.current()) return false;
            const meta = checkFileBody(decodeSealedPlain(content, KIND_FILE));
            // Header consumption is distinct from attachment/history completion.
            // The encrypted receive journal now owns the file key across reload.
            if (await sendCommit(headerCommit(commit)) === false || !ctx.current()) return false;
            const plain = await openFileBytes(bytes, meta);
            if (!ctx.current()) return false;
            const opened = { ...message, e2e: false, content: '📎 ' + meta.filename,
                file: { filename: meta.filename, mime_type: meta.mime_type, size: plain.length,
                    data_b64: b64(plain), spoiler: meta.spoiler,
                    ...(meta.width ? { width: meta.width, height: meta.height } : {}) },
                _persistDm: true, _dmHistoryManaged: true };
            if (await persistMessage(opened, ctx.current) === false || !ctx.current()) return false;
            if (!await receipts.remember(ctx.receiptScope, receiveId, null, commit) || !ctx.current()) return false;
            if (await sendCommit(commit) === false || !ctx.current()) return false;
            if (!await e2e.finishDmReceive(event.from_webid, headerId, ctx.current) || !ctx.current()) return false;
            await handleEvent(opened);
            return true;
        });
        queues.set(key, run);
        try { return await run; }
        finally { if (queues.get(key) === run) queues.delete(key); }
    }
    async function recover() {
        const ctx = context();
        if (!ctx.current()) return;
        for (const row of await e2e.pendingDmReceives({ transport: 'native-media' })) {
            if (!ctx.current()) return;
            if (row.data.scope !== ctx.scope || !row.data.commit) continue;
            // No attachment ciphertext is in localStorage. Re-acknowledge saved
            // headers and request the gateway's still-retained opaque bytes.
            try {
                checkFileBody(decodeSealedPlain(row.content, KIND_FILE));
                await sendCommit(headerCommit(row.data.commit));
            } catch (err) { onError(err); }
        }
        if (!ctx.current()) return;
        for (const commit of await receipts.pendingCommits(ctx.receiptScope)) {
            if (!ctx.current()) return;
            await sendCommit(commit);
        }
        if (ctx.current()) await requestReplay();
    }
    return { receive, recover,
        async confirmCommit(event) {
            const ctx = context();
            if (!ctx.current() || event?.type !== 'native_media_committed'
                || !/^[a-f0-9]{64}$/.test(event.receive_id || '')) return false;
            return receipts.confirmCommit(ctx.receiptScope, event.receive_id, event);
        },
        stop() { generation++; },
    };
}
