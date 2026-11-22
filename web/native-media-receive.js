import { dmEnvelopeFingerprint } from './dm-receipts.js';
import { checkFileBody, decodeSealedPlain, KIND_FILE, openFileBytes } from './e2e-secondary.js';
import { NATIVE_INLINE_BYTES } from './native-media-drafts.js';
import { NATIVE_FILE_BYTES } from './native-media-protocol.js';

const SEALED_KEYS = ['e2e', 'e2e_v', 'content', 'nonce', 'msg_num', 'pn', 'ratchet_pub'];
const hex = bytes => [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
const b64 = bytes => {
    const chunks = [];
    for (let offset = 0; offset < bytes.length; offset += 32768) chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 32768)));
    return btoa(chunks.join(''));
};

// Hash ciphertext separately: the encrypted ratchet recovery record must never
// contain an attachment's bytes or its large base64 socket representation.
export async function nativeMediaIdentity(event, ciphertextDigest) {
    const token = event?.native_commit;
    if (event?.native_media_v !== 1 || event.type !== 'message' || event.room_id
        || typeof event.from_webid !== 'string' || !event.from_webid.startsWith('did:key:') || event.from_webid.length > 2048
        || !/^[A-Za-z0-9_-]{1,128}$/.test(event.message_id || '')
        || token?.v !== 1 || !Number.isSafeInteger(token.queue_id) || token.queue_id < 1
        || !/^[a-f0-9]{64}$/.test(token.envelope_digest || '') || !/^[a-f0-9]{64}$/.test(ciphertextDigest || '')
        || event.e2e !== true || event.e2e_v !== 1 || typeof event.content !== 'string'
        || event.content.length > 4096 || typeof event.nonce !== 'string'
        || typeof event.ratchet_pub !== 'string' || !Number.isSafeInteger(event.msg_num) || event.msg_num < 0
        || !Number.isSafeInteger(event.pn) || event.pn < 0) throw Error('Invalid native media envelope');
    const header = Object.fromEntries(SEALED_KEYS.map(key => [key, event[key]]));
    const headerId = await dmEnvelopeFingerprint({ transport: 'native-media-header',
        from_webid: event.from_webid, message_id: event.message_id, header });
    const receiveId = await dmEnvelopeFingerprint({ transport: 'native-media', from_webid: event.from_webid,
        message_id: event.message_id, header, ciphertext_digest: ciphertextDigest });
    const commit = { cmd: 'native_media_commit', v: 1, queue_id: token.queue_id,
        from_webid: event.from_webid, message_id: event.message_id,
        envelope_digest: token.envelope_digest, receive_id: receiveId };
    return { receiveId, headerId, commit };
}

export async function nativeMediaEnvelope(event) {
    const file = event?.file;
    if (file?.filename !== 'file' || file.mime_type !== 'application/octet-stream'
        || !Number.isSafeInteger(file.size) || file.size < 17 || file.size > NATIVE_INLINE_BYTES
        || typeof file.data_b64 !== 'string' || file.data_b64.length > 699052) throw Error('Invalid native media ciphertext');
    const bytes = Uint8Array.from(atob(file.data_b64), ch => ch.charCodeAt(0));
    if (bytes.length !== file.size || b64(bytes) !== file.data_b64) throw Error('Invalid native media ciphertext');
    const ciphertextDigest = hex(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)));
    return { bytes, ...await nativeMediaIdentity(event, ciphertextDigest) };
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
    async function serialized(ctx, identity, work) {
        const key = JSON.stringify([ctx.scope, identity.headerId]);
        if (!queues.has(key) && queues.size >= 10) return false;
        const run = (queues.get(key) || Promise.resolve()).catch(() => {}).then(() => ctx.current() ? work() : false);
        queues.set(key, run);
        try { return await run; }
        finally { if (queues.get(key) === run) queues.delete(key); }
    }
    async function prepare(event, identity, ctx) {
        const { receiveId, headerId, commit } = identity;
        if (await receipts.has(ctx.receiptScope, receiveId)) {
            if (!ctx.current() || !await receipts.remember(ctx.receiptScope, receiveId, null, commit) || !ctx.current()
                || await sendCommit(commit) === false || !ctx.current()) return false;
            if (!await e2e.finishDmReceive(event.from_webid, headerId, ctx.current)) return false;
            return { complete: true };
        }
        if (!ctx.current() || !await receipts.hasSpace(ctx.receiptScope) || !ctx.current()) return false;
        const message = toMessage(event);
        if (!message?.message_id || !message.thread_id) return false;
        const data = { transport: 'native-media', scope: ctx.scope, commit };
        if (typeof event.timestamp === 'string' && event.timestamp.length <= 64) data.timestamp = event.timestamp;
        const content = await e2e.ratchetDecrypt(event.from_webid, event.content, event.nonce,
            event.msg_num, event.ratchet_pub, event.pn, { receiveId: headerId, receiveData: data, current: ctx.current });
        if (!ctx.current()) return false;
        const meta = checkFileBody(decodeSealedPlain(content, KIND_FILE));
        if (await sendCommit(headerCommit(commit)) === false || !ctx.current()) return false;
        return { complete: false, meta, message };
    }
    async function complete(event, envelope, ctx, persistFile) {
        const prepared = await prepare(event, envelope, ctx);
        if (!prepared) return false;
        if (prepared.complete) return true;
        const { meta, message } = prepared;
        const plain = await openFileBytes(envelope.bytes, meta);
        if (!ctx.current()) return false;
        const opened = { ...message, e2e: false, content: '📎 ' + meta.filename,
            file: { filename: meta.filename, mime_type: meta.mime_type, size: plain.length,
                ...(persistFile ? {} : { data_b64: b64(plain) }), spoiler: meta.spoiler,
                ...(meta.width ? { width: meta.width, height: meta.height } : {}) },
            _persistDm: true, _dmHistoryManaged: true };
        // Chunk callers persist a Blob and its history reference atomically,
        // then provide the readable history record for presentation.
        let presented = opened;
        if (persistFile) {
            const saved = await persistFile(opened, new Blob([plain], { type: meta.mime_type }), ctx.current);
            if (!saved || !ctx.current()) return false;
            presented = saved === true ? opened : { ...opened, ...saved };
            presented.file = { ...opened.file, ...presented.file,
                data_b64: typeof presented.file?.data_b64 === 'string' ? presented.file.data_b64 : b64(plain) };
            delete presented.file.unavailable;
        } else if (await persistMessage(opened, ctx.current) === false || !ctx.current()) return false;
        const { receiveId, headerId, commit } = envelope;
        if (!await receipts.remember(ctx.receiptScope, receiveId, null, commit) || !ctx.current()) return false;
        if (await sendCommit(commit) === false || !ctx.current()) return false;
        if (!await e2e.finishDmReceive(event.from_webid, headerId, ctx.current) || !ctx.current()) return false;
        await handleEvent(presented);
        return true;
    }
    async function receive(event) {
        const ctx = context();
        if (!ctx.current()) return false;
        const envelope = await nativeMediaEnvelope(event);
        if (!ctx.current()) return false;
        return serialized(ctx, envelope, () => complete(event, envelope, ctx));
    }
    async function receiveHeader(event, ciphertextDigest) {
        const ctx = context();
        if (!ctx.current()) return false;
        const identity = await nativeMediaIdentity(event, ciphertextDigest);
        if (!ctx.current()) return false;
        return serialized(ctx, identity, () => prepare(event, identity, ctx));
    }
    async function hasReceipt(event, ciphertextDigest) {
        const ctx = context();
        if (!ctx.current()) return false;
        const identity = await nativeMediaIdentity(event, ciphertextDigest);
        if (!ctx.current() || !await receipts.has(ctx.receiptScope, identity.receiveId) || !ctx.current()) return false;
        return identity;
    }
    async function receiveBytes(event, bytes, persistFile) {
        const ctx = context();
        if (!ctx.current()) return false;
        if (!(bytes instanceof Uint8Array) || bytes.length < 17 || bytes.length > NATIVE_FILE_BYTES || typeof persistFile !== 'function') throw Error('Invalid native file bytes');
        const hash = hex(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)));
        const identity = await nativeMediaIdentity(event, hash);
        if (!ctx.current()) return false;
        return serialized(ctx, identity, () => complete(event, { ...identity, bytes }, ctx, persistFile));
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
    return { receive, receiveHeader, receiveBytes, hasReceipt, recover,
        async confirmCommit(event) {
            const ctx = context();
            if (!ctx.current() || event?.type !== 'native_media_committed'
                || !/^[a-f0-9]{64}$/.test(event.receive_id || '')) return false;
            return receipts.confirmCommit(ctx.receiptScope, event.receive_id, event);
        },
        stop() { generation++; },
    };
}
