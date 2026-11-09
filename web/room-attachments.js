// Immutable, room-owned JSON chunks. The signed Long Chat message binds the
// complete manifest; chunks inherit the room's Read/Append ACL.
import { u8ToB64, b64ToU8 } from './util.js';
import { FILE_MIMES } from './e2e-secondary.js';
export const ROOM_ASSET_MAX = 2 * 1024 * 1024;
export const ROOM_ASSET_CHUNK = 32 * 1024;
export const ROOM_ASSET_PREFIX = '[proxion-attachment:v1]';
const encoder = new TextEncoder();
const idPattern = /^[\w-]{1,128}$/;
const assetPath = /^asset-[\w-]{1,160}\.json$/;
const hex = bytes => [...bytes].map(value => value.toString(16).padStart(2, '0')).join('');
const digest = async bytes => hex(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)));
export function assetUrl(container, value) {
    return typeof value === 'string' && value.startsWith(container) && assetPath.test(value.slice(container.length));
}
export function parseRoomAttachment(content, container = null) {
    if (typeof content !== 'string' || !content.startsWith(ROOM_ASSET_PREFIX) || encoder.encode(content).length > 24000) return null;
    try {
        const doc = JSON.parse(content.slice(ROOM_ASSET_PREFIX.length));
        if (doc.v !== 1 || typeof doc.id !== 'string' || !idPattern.test(doc.id) || typeof doc.name !== 'string' || !doc.name || doc.name.length > 255
            || /[\x00-\x1f/\\]/.test(doc.name) || !FILE_MIMES.has(doc.mime)
            || !Number.isInteger(doc.size) || doc.size < 0 || doc.size > ROOM_ASSET_MAX || !/^[a-f0-9]{64}$/.test(doc.sha256 || '')
            || !['file', 'voice'].includes(doc.kind) || !Array.isArray(doc.chunks)
            || doc.chunks.length !== Math.max(1, Math.ceil(doc.size / ROOM_ASSET_CHUNK))
            || new Set(doc.chunks).size !== doc.chunks.length
            || doc.chunks.some(url => typeof url !== 'string' || url.length > 2048 || (container && !assetUrl(container, url) && !assetPath.test(url)))) return null;
        if (doc.kind === 'voice' && (doc.size === 0 || doc.mime !== 'audio/webm' || !Number.isInteger(doc.durationMs) || doc.durationMs < 500 || doc.durationMs > 60000)) return null;
        if (doc.spoiler !== undefined && typeof doc.spoiler !== 'boolean') return null;
        return container ? { ...doc, chunks: doc.chunks.map(url => assetUrl(container, url) ? url : container + url) } : doc;
    } catch { return null; }
}
export function createRoomAttachments({ fetch, current = () => true }) {
    const failure = (reason = 'unavailable') => ({ ok: false, reason });
    async function read(url, id, index) {
        if (!current()) return failure('context_changed');
        try {
            const response = await fetch(url, { headers: { Accept: 'application/json' }, cache: 'no-store', redirect: 'error' });
            if (!current()) return failure('context_changed');
            if (!response.ok || Number(response.headers?.get('content-length')) > 48000) return failure();
            let text;
            if (response.body?.getReader) {
                const reader = response.body.getReader(), parts = []; let length = 0;
                try {
                    while (true) {
                        const part = await reader.read(); if (part.done) break;
                        length += part.value.byteLength;
                        if (length > 48000 || !current()) { await reader.cancel(); return failure('limit'); }
                        parts.push(part.value);
                    }
                    const joined = new Uint8Array(length); let offset = 0;
                    for (const part of parts) { joined.set(part, offset); offset += part.byteLength; }
                    text = new TextDecoder('utf-8', { fatal: true }).decode(joined);
                } finally { reader.releaseLock(); }
            } else text = await response.text();
            if (encoder.encode(text).length > 48000 || !current()) return failure();
            const doc = JSON.parse(text);
            if (doc.v !== 1 || doc.id !== id || doc.index !== index || typeof doc.data !== 'string'
                || doc.data.length > 43692 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(doc.data)) return failure('invalid');
            const bytes = b64ToU8(doc.data);
            return bytes.length <= ROOM_ASSET_CHUNK ? { ok: true, bytes } : failure('invalid');
        } catch { return failure(); }
    }
    async function upload(container, blob, metadata, progress = () => {}, completed = []) {
        if (!current()) return failure('context_changed');
        if (!blob || blob.size > ROOM_ASSET_MAX) return failure('limit');
        const bytes = new Uint8Array(await blob.arrayBuffer());
        if (bytes.length > ROOM_ASSET_MAX || !current()) return failure('limit');
        const doc = { v: 1, id: metadata.id, name: metadata.name, mime: metadata.mime, kind: metadata.kind || 'file',
            size: bytes.length, sha256: await digest(bytes), chunks: [], spoiler: metadata.spoiler === true, ...(metadata.durationMs ? { durationMs: metadata.durationMs } : {}) };
        const count = Math.max(1, Math.ceil(bytes.length / ROOM_ASSET_CHUNK));
        const candidate = { ...doc, chunks: Array.from({ length: count }, (_, index) => `asset-${doc.id}-${index}.json`) };
        if (!parseRoomAttachment(ROOM_ASSET_PREFIX + JSON.stringify(candidate), container)) return failure('invalid');
        for (let index = 0; index < count; index++) {
            if (!current()) return failure('context_changed');
            const part = bytes.slice(index * ROOM_ASSET_CHUNK, (index + 1) * ROOM_ASSET_CHUNK);
            let url = completed[index];
            if (!assetUrl(container, url)) {
                const body = JSON.stringify({ v: 1, id: doc.id, index, data: u8ToB64(part) });
                let response;
                try { response = await fetch(container, { method: 'POST', redirect: 'error', headers: {
                    'Content-Type': 'application/json', Slug: `asset-${doc.id}-${index}.json`,
                }, body }); } catch { return failure(); }
                if (!current()) return failure('context_changed');
                if (!response.ok) return failure();
                try { url = new URL(response.headers.get('location'), container).href; } catch { return failure('invalid'); }
                if (!assetUrl(container, url)) return failure('invalid');
            }
            const result = await read(url, doc.id, index);
            if (!result.ok || await digest(result.bytes) !== await digest(part)) return failure('readback_failed');
            doc.chunks.push(url);
            await progress([...doc.chunks], index + 1, count);
        }
        const content = ROOM_ASSET_PREFIX + JSON.stringify({ ...doc, chunks: doc.chunks.map(url => url.slice(container.length)) });
        const manifest = parseRoomAttachment(content, container);
        return current() && manifest ? { ok: true, manifest, content } : failure('invalid');
    }
    async function download(container, content) {
        const doc = parseRoomAttachment(content, container);
        if (!doc || !current()) return failure('invalid');
        const bytes = new Uint8Array(doc.size); let offset = 0;
        for (let index = 0; index < doc.chunks.length; index++) {
            const part = await read(doc.chunks[index], doc.id, index);
            if (!part.ok || part.bytes.length !== Math.min(ROOM_ASSET_CHUNK, doc.size - offset)) return failure('invalid');
            bytes.set(part.bytes, offset); offset += part.bytes.length;
        }
        if (!current() || await digest(bytes) !== doc.sha256) return failure('integrity');
        return { ok: true, bytes, manifest: doc };
    }
    return { upload, download };
}
