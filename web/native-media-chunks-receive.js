import { createNativeMediaInbox, nativeChunkManifest } from './native-media-inbox.js';
import { nativeMediaIdentity } from './native-media-receive.js';
import { dmEnvelopeFingerprint } from './dm-receipts.js';

const asMessage = row => ({ ...row.manifest, type: 'message', native_media_v: 1,
    from_webid: row.sender, message_id: row.id, thread_id: row.threadHint || row.sender,
    timestamp: row.timestamp, native_commit: row.token });

// Pull windows advance only after the preceding parts commit locally. Gateway
// custody is retained until the ordinary protected full-file receive receipt.
export function createNativeChunkReceiver({ receiver, getScope, getGeneration = () => 0, isActive,
    getOwnPub, send, persistFile, inbox = createNativeMediaInbox(), onState = () => {},
    onError = err => console.warn('[native-chunks] receive pending:', err), now = Date.now }) {
    let epoch = 0;
    const entries = new Map();
    const context = () => {
        const scope = getScope(), generation = getGeneration(), stamp = epoch;
        return { scope, current: () => stamp === epoch && scope === getScope() && generation === getGeneration() && isActive() };
    };
    const keyFor = row => JSON.stringify([row.scope, row.sender, row.id]);
    const notify = (row, state) => onState({ key: row.key || keyFor(row), id: row.id, peer: row.sender, scope: row.scope,
        state, received: row.received || 0, total: row.manifest.chunk_count });
    async function release(row, event, ctx, state = 'committed') {
        const identity = await nativeMediaIdentity(event, row.manifest.cipher_digest);
        if (!ctx.current()) return false;
        await inbox.release(ctx.scope, row.sender, row.id, row.manifestDigest, identity.receiveId, ctx.current);
        if (!ctx.current()) return false;
        entries.delete(keyFor(row)); notify(row, state); return true;
    }
    async function finish(entry, ctx) {
        if (entry.cancelled || entry.finalizing || !ctx.current()) return false;
        entry.finalizing = true;
        try {
            const { row } = entry, assembled = await inbox.assemble(ctx.scope, row.sender, row.id, row.manifestDigest, ctx.current);
            if (!ctx.current() || getOwnPub() !== row.manifest.recipient_pub) return false;
            const event = asMessage(row);
            if (!await receiver.receiveBytes(event, assembled.bytes, persistFile) || !ctx.current()) {
                if (ctx.current()) notify(row, 'history-pending'); return false;
            }
            return release(row, event, ctx);
        } catch (error) { if (ctx.current()) notify(entry.row, 'storage'); throw error; }
        finally { entry.finalizing = false; }
    }
    async function fetch(entry, ctx) {
        if (!ctx.current() || entry.cancelled || entry.requesting || entry.finalizing || (entry.window.size && now() < entry.deadline)) return false;
        entry.requesting = true;
        try {
            const { row } = entry;
            if (getOwnPub() !== row.manifest.recipient_pub) { notify(row, 'key-unavailable'); return false; }
            const missing = await inbox.missing(ctx.scope, row.sender, row.id, row.manifestDigest, ctx.current);
            if (!ctx.current()) return false;
            if (!missing.length) return finish(entry, ctx);
            entry.window = new Set(missing); entry.deadline = now() + 15000;
            const result = await send({ cmd: 'native_media_fetch', v: 1, from_webid: row.sender, message_id: row.id,
                queue_id: row.token.queue_id, envelope_digest: row.token.envelope_digest,
                manifest_digest: row.manifestDigest, sequences: missing });
            if (result === false) entry.window.clear();
            if (ctx.current()) notify(row, 'receiving');
            return result !== false && ctx.current();
        } finally { entry.requesting = false; }
    }
    async function offer(event) {
        const ctx = context();
        if (!ctx.current()) return false;
        const manifest = nativeChunkManifest(event);
        if (event.type !== 'native_media_offer' || event.native_chunks_v !== 1 || manifest.recipient_pub !== getOwnPub()) return false;
        if (await dmEnvelopeFingerprint(manifest) !== event.manifest_digest || !ctx.current()) throw Error('Native manifest mismatch');
        const message = { ...event, type: 'message', native_media_v: 1 };
        const known = await receiver.hasReceipt(message, manifest.cipher_digest);
        if (!ctx.current()) return false;
        if (known) {
            const prepared = await receiver.receiveHeader(message, manifest.cipher_digest);
            if (!prepared?.complete || !ctx.current()) return false;
            const row = { scope: ctx.scope, sender: event.from_webid, id: event.message_id,
                manifest, manifestDigest: event.manifest_digest };
            return release(row, message, ctx);
        }
        if (await receiver.hasDeclined(message, manifest.cipher_digest)) {
            if (!await receiver.decline(message, manifest.cipher_digest) || !ctx.current()) return false;
            return release({ scope: ctx.scope, sender: event.from_webid, id: event.message_id, manifest, manifestDigest: event.manifest_digest }, message, ctx, 'declined');
        }
        const row = await inbox.begin(ctx.scope, event, ctx.current);
        if (!ctx.current() || getOwnPub() !== row.manifest.recipient_pub) return false;
        const prepared = await receiver.receiveHeader(asMessage(row), row.manifest.cipher_digest);
        if (!prepared || !ctx.current()) return false;
        if (prepared.declined) return release(row, asMessage(row), ctx, 'declined');
        if (prepared.complete) return release(row, asMessage(row), ctx);
        const key = keyFor(row);
        let entry = entries.get(key);
        if (!entry) { entry = { row, window: new Set(), processing: new Set(), deadline: 0 }; entries.set(key, entry); }
        else entry.row = row;
        return fetch(entry, ctx);
    }
    async function part(event) {
        const ctx = context(), key = JSON.stringify([ctx.scope, event.from_webid, event.message_id]), entry = entries.get(key);
        if (!ctx.current() || !entry || entry.cancelled || event.native_chunks_v !== 1 || event.manifest_digest !== entry.row.manifestDigest
            || !entry.window.has(event.seq) || entry.processing.has(event.seq) || getOwnPub() !== entry.row.manifest.recipient_pub) return false;
        if (typeof event.data_b64 !== 'string' || event.data_b64.length > 87384) throw Error('Invalid native receive part');
        entry.processing.add(event.seq);
        try {
            const binary = atob(event.data_b64);
            if (btoa(binary) !== event.data_b64) throw Error('Noncanonical native receive part');
            const bytes = Uint8Array.from(binary, char => char.charCodeAt(0));
            if (bytes.length < 1 || bytes.length > 65536) throw Error('Invalid native receive part');
            const row = await inbox.put(ctx.scope, entry.row.sender, entry.row.id, entry.row.manifestDigest,
                event.seq, bytes, event.part_digest, ctx.current);
            if (!ctx.current() || entry.cancelled) return false;
            entry.row = row; entry.window.delete(event.seq);
            notify(row, 'receiving');
            if (!entry.window.size) return fetch(entry, ctx);
            return true;
        } finally { entry.processing.delete(event.seq); }
    }
    return { offer, part,
        async decline(sender, id) {
            const ctx = context();
            if (!ctx.current()) return false;
            const row = (await inbox.list(ctx.scope, ctx.current)).find(row => row.sender === sender && row.id === id);
            if (!row || !ctx.current() || row.manifest.recipient_pub !== getOwnPub()) return false;
            const entry = entries.get(keyFor(row));
            if (entry) entry.cancelled = true;
            let released = false;
            try {
                const declined = await receiver.decline(asMessage(row), row.manifest.cipher_digest);
                if (!declined || !ctx.current()) return false;
                released = await release(row, asMessage(row), ctx, 'declined');
                return released;
            } finally { if (entry && !released) entry.cancelled = false; }
        },
        async recover() {
            const ctx = context();
            if (!ctx.current()) return false;
            for (const row of await inbox.list(ctx.scope, ctx.current)) {
                if (!ctx.current()) return false;
                try { await offer({ ...row.manifest, type: 'native_media_offer', native_chunks_v: 1,
                    from_webid: row.sender, message_id: row.id, thread_id: row.threadHint,
                    timestamp: row.timestamp, manifest_digest: row.manifestDigest, native_commit: row.token }); }
                catch (err) { onError(err); }
            }
            return true;
        },
        stop() { epoch++; entries.clear(); },
    };
}
