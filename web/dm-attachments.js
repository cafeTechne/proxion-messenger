// Pod DM assets contain ciphertext only. The manifest (including the file key,
// name and MIME) MUST travel inside an encrypted DM, never as a pod resource.
import { encryptFile, openFileBytes, FILE_MIMES, isBlockedMagic, sanitizeFilename } from './e2e-secondary.js';
import { isDmPeerWebId, peerPodRootFromWebId } from './webdm.js';
import { accessControlUrl, detectAclModel } from './acl.js';
import { u8ToB64, b64ToU8 } from './util.js';
import { isPeerPodRootAllowed } from './ssrf.js';

export const DM_ASSET_MAX = 2 * 1024 * 1024;
export const DM_ASSET_CHUNK = 32768;
export const DM_ASSET_PATH = 'proxion/dm-assets/';
export const DM_ASSET_PREFIX = '[proxion-dm-attachment:v1]';
const ID = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const B64 = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;
const encoder = new TextEncoder();
const failure = reason => ({ ok: false, reason });
const digest = async bytes => [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))]
    .map(value => value.toString(16).padStart(2, '0')).join('');
const chunkName = (id, index) => `asset-${id}-${index}.json`;

// No URLs are accepted from the manifest. Derive every resource from the
// authenticated message sender's WebID and the validated random asset id.
export function dmAssetContainer(senderWebId) {
    if (!isDmPeerWebId(senderWebId)) return null;
    const root = peerPodRootFromWebId(senderWebId);
    try {
        const url = new URL(root);
        if (url.href !== root || url.username || url.password || url.search || url.hash
            || /[%\\\s]/.test(root) || !root.endsWith('/')) return null;
        return root + DM_ASSET_PATH;
    } catch { return null; }
}

export function parseDmAttachment(content) {
    if (typeof content !== 'string' || !content.startsWith(DM_ASSET_PREFIX) || encoder.encode(content).length > 4096) return null;
    try {
        const doc = JSON.parse(content.slice(DM_ASSET_PREFIX.length));
        if (!doc || doc.v !== 1 || typeof doc.id !== 'string' || !ID.test(doc.id)
            || typeof doc.name !== 'string' || doc.name !== sanitizeFilename(doc.name) || !doc.name
            || !FILE_MIMES.has(doc.mime) || !Number.isInteger(doc.size) || doc.size < 0 || doc.size > DM_ASSET_MAX
            || typeof doc.key !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(doc.key)
            || typeof doc.iv !== 'string' || !/^[A-Za-z0-9_-]{16}$/.test(doc.iv)
            || typeof doc.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(doc.sha256)
            || !['file', 'voice'].includes(doc.kind) || doc.chunks !== Math.ceil((doc.size + 16) / DM_ASSET_CHUNK)
            || (doc.spoiler !== undefined && typeof doc.spoiler !== 'boolean')
            || (doc.kind === 'voice' && (doc.size === 0 || doc.mime !== 'audio/webm'
                || !Number.isInteger(doc.durationMs) || doc.durationMs < 500 || doc.durationMs > 60000))) return null;
        // Reject additional URL/transport fields rather than accidentally giving
        // a future renderer an unvalidated source or leaking metadata on upload.
        const allowed = new Set(['v', 'id', 'name', 'mime', 'size', 'key', 'iv', 'sha256', 'kind', 'chunks', 'spoiler', 'durationMs']);
        return Object.keys(doc).every(key => allowed.has(key)) ? doc : null;
    } catch { return null; }
}

// Persist this prepared object before upload. Reusing its ciphertext and key
// makes retries (including a lost PUT acknowledgement) deterministic; preparing
// again would replace the key while retaining the old resource names.
export async function prepareDmAttachment(blob, metadata, { id = crypto.randomUUID(), current = () => true } = {}) {
    if (!metadata || typeof metadata !== 'object') return failure('invalid');
    if (!blob || !Number.isInteger(blob.size) || blob.size < 0 || blob.size > DM_ASSET_MAX || !current()) return failure('limit');
    const candidate = { v: 1, id, name: metadata.name, mime: metadata.mime, kind: metadata.kind || 'file', size: blob.size,
        key: 'A'.repeat(43), iv: 'A'.repeat(16), sha256: '0'.repeat(64), chunks: Math.ceil((blob.size + 16) / DM_ASSET_CHUNK),
        spoiler: metadata.spoiler === true, ...(metadata.durationMs !== undefined ? { durationMs: metadata.durationMs } : {}) };
    if (!parseDmAttachment(DM_ASSET_PREFIX + JSON.stringify(candidate))) return failure('invalid');
    try {
        const bytes = new Uint8Array(await blob.arrayBuffer());
        if (!current() || bytes.length !== blob.size) return failure('context_changed');
        if (isBlockedMagic(bytes)) return failure('invalid');
        const sealed = await encryptFile(bytes);
        const manifest = { ...candidate, key: sealed.key, iv: sealed.iv, sha256: await digest(sealed.ct) };
        return current() ? { ok: true, ciphertext: new Blob([sealed.ct]), content: DM_ASSET_PREFIX + JSON.stringify(manifest) } : failure('context_changed');
    } catch { return failure('unavailable'); }
}

// WAC and ACP expose ciphertext reads, never public writes. ACP public Read
// must reach children too (the generic drop-box ACL intentionally doesn't).
export function dmAssetAcl(container, owner, model = 'wac') {
    if (dmAssetContainer(owner) !== container) throw Error('invalid asset owner');
    if (model === 'acp') return '@prefix acp: <http://www.w3.org/ns/solid/acp#>.\n@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n'
        + `<> a acp:AccessControlResource; acp:resource <${container}>; acp:accessControl <#owner-ac>, <#public-ac>; acp:memberAccessControl <#owner-ac>, <#public-ac>.\n`
        + '<#owner-ac> a acp:AccessControl; acp:apply <#owner-policy>.\n'
        + '<#owner-policy> a acp:Policy; acp:allow acl:Read, acl:Write, acl:Control; acp:anyOf <#owner-matcher>.\n'
        + `<#owner-matcher> a acp:Matcher; acp:agent <${owner}>.\n`
        + '<#public-ac> a acp:AccessControl; acp:apply <#public-policy>.\n'
        + '<#public-policy> a acp:Policy; acp:allow acl:Read; acp:anyOf <#public-matcher>.\n'
        + '<#public-matcher> a acp:Matcher; acp:agent acp:PublicAgent.\n';
    return '@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n@prefix foaf: <http://xmlns.com/foaf/0.1/>.\n'
        + `<#owner> a acl:Authorization; acl:agent <${owner}>; acl:accessTo <${container}>; acl:default <${container}>; acl:mode acl:Read, acl:Write, acl:Control.\n`
        + `<#public> a acl:Authorization; acl:agentClass foaf:Agent; acl:accessTo <${container}>; acl:default <${container}>; acl:mode acl:Read.\n`;
}

async function cappedJson(response, current) {
    if (!response?.ok || Number(response.headers?.get('content-length')) > 48000) return null;
    let text;
    if (response.body?.getReader) {
        const reader = response.body.getReader(), parts = []; let length = 0;
        try {
            while (true) {
                const part = await reader.read(); if (part.done) break;
                length += part.value.byteLength;
                if (length > 48000 || !current()) { await reader.cancel(); return null; }
                parts.push(part.value);
            }
            const bytes = new Uint8Array(length); let offset = 0;
            for (const part of parts) { bytes.set(part, offset); offset += part.byteLength; }
            text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
        } finally { reader.releaseLock(); }
    } else text = await response.text();
    if (!current() || encoder.encode(text).length > 48000) return null;
    return JSON.parse(text);
}

export function createDmAttachments({ fetch, current = () => true, getSelfWebId = () => null }) {
    async function ensure(container, owner) {
        let response = await fetch(container, { method: 'HEAD', redirect: 'error', cache: 'no-store' });
        if (!current()) return false;
        if (response.status === 404) {
            response = await fetch(container, { method: 'PUT', redirect: 'error', headers: {
                'Content-Type': 'text/turtle', Link: '<http://www.w3.org/ns/ldp#BasicContainer>; rel="type"', 'If-None-Match': '*',
            }, body: '' });
            if (!current() || !(response.ok || response.status === 412 || response.status === 409)) return false;
            response = await fetch(container, { method: 'HEAD', redirect: 'error', cache: 'no-store' });
        }
        if (!current() || !response.ok) return false;
        const link = response.headers?.get('link'), acl = accessControlUrl(link, container) || container + '.acl';
        // A malicious Link must not turn an asset upload into an unrelated ACL write.
        if (![container + '.acl', container + '.acr'].includes(acl)) return false;
        const model = detectAclModel(link, acl) || 'wac';
        const grant = await fetch(acl, { method: 'PUT', redirect: 'error', headers: { 'Content-Type': 'text/turtle' }, body: dmAssetAcl(container, owner, model) });
        return current() && grant.ok;
    }

    async function read(url, id, index) {
        if (!current()) return null;
        const doc = await cappedJson(await fetch(url, { headers: { Accept: 'application/json' }, redirect: 'error', cache: 'no-store' }), current);
        if (!doc || doc.v !== 1 || doc.id !== id || doc.index !== index || typeof doc.data !== 'string'
            || doc.data.length > 43692 || !B64.test(doc.data) || Object.keys(doc).sort().join(',') !== 'data,id,index,v') return null;
        const bytes = b64ToU8(doc.data);
        return bytes.length <= DM_ASSET_CHUNK && u8ToB64(bytes) === doc.data ? bytes : null;
    }

    async function upload(owner, prepared, progress = () => {}) {
        const container = dmAssetContainer(owner), manifest = parseDmAttachment(prepared?.content);
        if (!container || !manifest || !prepared?.ciphertext || prepared.ciphertext.size !== manifest.size + 16 || !current()) return failure('invalid');
        try {
            const bytes = new Uint8Array(await prepared.ciphertext.arrayBuffer());
            if (bytes.length !== manifest.size + 16 || await digest(bytes) !== manifest.sha256 || !current()) return failure('integrity');
            if ((await openFileBytes(bytes, { key: manifest.key, iv: manifest.iv })).length !== manifest.size || !current()) return failure('integrity');
            if (!await ensure(container, owner)) return failure('storage_unavailable');
            for (let index = 0; index < manifest.chunks; index++) {
                if (!current()) return failure('context_changed');
                const part = bytes.slice(index * DM_ASSET_CHUNK, (index + 1) * DM_ASSET_CHUNK), url = container + chunkName(manifest.id, index);
                const response = await fetch(url, { method: 'PUT', redirect: 'error', headers: {
                    'Content-Type': 'application/json', 'If-None-Match': '*',
                }, body: JSON.stringify({ v: 1, id: manifest.id, index, data: u8ToB64(part) }) });
                if (!current()) return failure('context_changed');
                if (!(response.ok || response.status === 412)) return failure('upload_failed');
                const stored = await read(url, manifest.id, index);
                if (!stored || stored.length !== part.length || await digest(stored) !== await digest(part)) return failure('readback_failed');
                if (!current()) return failure('context_changed');
                await progress(index + 1, manifest.chunks);
            }
            return current() ? { ok: true, content: prepared.content, manifest } : failure('context_changed');
        } catch { return failure('unavailable'); }
    }

    async function download(sender, content) {
        const container = dmAssetContainer(sender), manifest = parseDmAttachment(content);
        if (!container || !manifest || !current()) return failure('invalid');
        if (!isPeerPodRootAllowed(container, dmAssetContainer(getSelfWebId()))) return failure('unsafe_peer');
        try {
            const ciphertext = new Uint8Array(manifest.size + 16); let offset = 0;
            for (let index = 0; index < manifest.chunks; index++) {
                const part = await read(container + chunkName(manifest.id, index), manifest.id, index);
                if (!part || part.length !== Math.min(DM_ASSET_CHUNK, ciphertext.length - offset)) return failure('invalid');
                ciphertext.set(part, offset); offset += part.length;
            }
            if (!current() || await digest(ciphertext) !== manifest.sha256) return failure('integrity');
            const bytes = await openFileBytes(ciphertext, { key: manifest.key, iv: manifest.iv });
            return current() && bytes.length === manifest.size ? { ok: true, bytes, manifest } : failure('integrity');
        } catch { return failure('unavailable'); }
    }

    // Only the draft controller can establish that delivery was never attempted.
    // Verify the original ciphertext and the current resource version before
    // deleting; this API never lists a container or guesses unused resources.
    async function discardUnused(owner, prepared, progress = () => {}) {
        const container = dmAssetContainer(owner), manifest = parseDmAttachment(prepared?.content);
        if (getSelfWebId() !== owner || !container || !manifest || !current()
            || prepared?.ciphertext?.size !== manifest.size + 16) return failure('invalid');
        try {
            const bytes = new Uint8Array(await prepared.ciphertext.arrayBuffer());
            if (bytes.length !== manifest.size + 16 || await digest(bytes) !== manifest.sha256) return failure('integrity');
            for (let index = 0; index < manifest.chunks; index++) {
                if (!current()) return failure('context_changed');
                const url = container + chunkName(manifest.id, index);
                const response = await fetch(url, { headers: { Accept: 'application/json' }, redirect: 'error', cache: 'no-store' });
                if (!current()) return failure('context_changed');
                if (![404, 410].includes(response.status)) {
                    const etag = response.headers?.get('etag');
                    if (typeof etag !== 'string' || !/^"[\x21\x23-\x7e\x80-\xff]{0,256}"$/.test(etag)) return failure('version_unavailable');
                    const doc = await cappedJson(response, current);
                    const part = bytes.slice(index * DM_ASSET_CHUNK, (index + 1) * DM_ASSET_CHUNK);
                    if (!doc || doc.v !== 1 || doc.id !== manifest.id || doc.index !== index
                        || Object.keys(doc).sort().join(',') !== 'data,id,index,v' || doc.data !== u8ToB64(part)) return failure('integrity');
                    if (!current()) return failure('context_changed');
                    const deleted = await fetch(url, { method: 'DELETE', headers: { 'If-Match': etag }, redirect: 'error' });
                    if (![200, 204, 205, 404, 410].includes(deleted.status)) return failure('delete_failed');
                }
                if (!current()) return failure('context_changed');
                await progress(index + 1, manifest.chunks);
            }
            return { ok: true };
        } catch { return failure('unavailable'); }
    }
    return { upload, download, discardUnused };
}
