import { dmEnvelopeFingerprint } from './dm-receipts.js';
import { nativeChunkManifest, NATIVE_CHUNK_BYTES } from './native-media-protocol.js';

const b64 = bytes => {
    const pieces = [];
    for (let i = 0; i < bytes.length; i += 32768) pieces.push(String.fromCharCode(...bytes.subarray(i, i + 32768)));
    return btoa(pieces.join(''));
};
const hex = bytes => [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, '0')).join('');

// Upload ACKs prove gateway custody only. The caller separately handles exact
// header consumption and retained recipient history before retiring the source.
export function createNativeChunkUploader({ send, getScope, getGeneration = () => 0, isActive,
    getPeerPub, onProgress = async () => {}, onState = () => {}, now = Date.now }) {
    let epoch = 0;
    const uploads = new Map(), starting = new Map();
    function context(row, outer) {
        const scope = getScope(), generation = getGeneration(), stamp = epoch;
        return () => outer() && isActive() && scope === row.scope && scope === getScope()
            && generation === getGeneration() && stamp === epoch && getPeerPub(row.peer) === row.peerPub;
    }
    const keyFor = row => JSON.stringify([row.scope, row.id]);
    async function transmit(entry, command, phase) {
        if (!entry.current()) return false;
        entry.phase = phase; entry.deadline = now() + 15000;
        try {
            const sent = await send(command, entry.current);
            if (sent === false) entry.deadline = 0;
            return sent !== false && entry.current();
        } catch (err) { entry.deadline = 0; throw err; }
    }
    async function begin(row, identity, outer) {
        const current = context(row, outer);
        if (!current() || row.stage !== 'sealed' || row.receiveHold !== true || !(row.prepared?.ct instanceof Uint8Array)) return false;
        const key = keyFor(row), existing = uploads.get(key);
        if (existing?.current() && now() < existing.deadline) return true;
        if (!existing && uploads.size >= 10) return false;
        const bytes = row.prepared.ct;
        const manifest = nativeChunkManifest({ ...row.hdr, size: bytes.length,
            cipher_digest: hex(await crypto.subtle.digest('SHA-256', bytes)),
            chunk_count: Math.ceil(bytes.length / NATIVE_CHUNK_BYTES), recipient_pub: row.peerPub });
        const manifestDigest = await dmEnvelopeFingerprint(manifest);
        if (!current()) return false;
        if (existing && (existing.manifestDigest !== manifestDigest || existing.identity.mediaDigest !== identity.mediaDigest
            || existing.identity.headerDigest !== identity.headerDigest)) throw Error('Conflicting native upload');
        const entry = { row, identity, manifest, manifestDigest, current, deadline: 0, busy: false };
        uploads.set(key, entry);
        onState(row.id, 'uploading');
        return transmit(entry, { cmd: 'native_media_begin', v: 1, cert_id: row.view.id,
            message_id: row.id, media_digest: identity.mediaDigest, manifest }, 'begin');
    }
    function start(row, identity, outer = () => true) {
        for (const [key, entry] of uploads) if (!entry.current()) uploads.delete(key);
        const key = keyFor(row);
        if (starting.has(key)) return starting.get(key);
        const reserved = new Set([...uploads.keys(), ...starting.keys()]);
        if (!reserved.has(key) && reserved.size >= 10) return Promise.resolve(false);
        const run = begin(row, identity, outer);
        starting.set(key, run);
        run.finally(() => { if (starting.get(key) === run) starting.delete(key); }).catch(() => {});
        return run;
    }
    async function status(event) {
        if (event?.type !== 'native_media_parts' || event.v !== 1 || typeof event.message_id !== 'string') return false;
        const key = JSON.stringify([getScope(), event.message_id]), entry = uploads.get(key);
        if (!entry || entry.busy || !entry.current() || event.media_digest !== entry.identity.mediaDigest
            || event.header_digest !== entry.identity.headerDigest || event.manifest_digest !== entry.manifestDigest
            || event.recipient_pub !== entry.row.peerPub
            || typeof event.custody_complete !== 'boolean' || typeof event.header_committed !== 'boolean' || typeof event.committed !== 'boolean'
            || (event.committed && !event.header_committed) || (event.header_committed && !event.custody_complete)
            || !Array.isArray(event.missing) || event.missing.length > 8
            || !event.missing.every(seq => Number.isSafeInteger(seq) && seq >= 0 && seq < entry.manifest.chunk_count)
            || new Set(event.missing).size !== event.missing.length
            || (event.custody_complete ? event.missing.length !== 0 : event.missing.length === 0)
            || (entry.phase === 'begin' ? Object.hasOwn(event, 'seq') : event.seq !== entry.phase)) return false;
        entry.busy = true;
        try {
            await onProgress({ type: 'native_media_status', v: 1, message_id: event.message_id,
                media_digest: event.media_digest, header_digest: event.header_digest,
                header_committed: event.header_committed, status: event.committed ? 'committed' : 'pending' }, entry.current);
            if (!entry.current()) return false;
            if (event.custody_complete) {
                uploads.delete(key); onState(entry.row.id, event.header_committed ? 'file-pending' : 'header-pending'); return true;
            }
            const seq = event.missing[0], bytes = entry.row.prepared.ct.subarray(seq * NATIVE_CHUNK_BYTES, (seq + 1) * NATIVE_CHUNK_BYTES);
            return await transmit(entry, { cmd: 'native_media_part', v: 1, message_id: entry.row.id,
                manifest_digest: entry.manifestDigest, seq, data_b64: b64(bytes) }, seq);
        } finally { entry.busy = false; }
    }
    return { start, status, stop() { epoch++; uploads.clear(); starting.clear(); } };
}
