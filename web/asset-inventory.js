// Read-only inventory of our own room storage. Directory listings are not proof
// of an unused file: no deletion or binary downloads are exposed here.
import { parseInboxListing, CONTAINS_PRED } from './ldn.js';
import { parseLinkHeader } from './acl.js';
export const INVENTORY_PAGE = 50;
const CAP = 512 * 1024;
const ROOM = /^[A-Za-z0-9_-]{1,128}$/;
const CHUNK = /^asset-[A-Za-z0-9_-]{1,160}\.json$/;
const NAME = /^[A-Za-z0-9._-]{1,255}$/;
const encoder = new TextEncoder();
const failed = reason => ({ ok: false, reason });
export function inventoryRoot(root) {
    try {
        const url = new URL(root);
        if (typeof root !== 'string' || root !== url.href || !root.endsWith('/') || url.search || url.hash || url.username || url.password
            || /[%\\\s]/.test(root) || !(url.protocol === 'https:' || (url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)))) return null;
        return root + 'proxion/rooms/';
    } catch { return null; }
}
const legacyName = name => NAME.test(name) && name !== '.' && name !== '..' && !/\.(acl|acr)$/i.test(name);
const validPath = path => path === '' || path === 'files/' || /^files\/[A-Za-z0-9_-]{1,128}\/$/.test(path);

async function cappedText(response, current) {
    if (!response.ok || Number(response.headers?.get('content-length')) > CAP) {
        await response.body?.cancel?.();
        throw Error('listing unavailable');
    }
    let text;
    if (response.body?.getReader) {
        const reader = response.body.getReader(), parts = []; let length = 0;
        try {
            while (true) {
                const part = await reader.read(); if (part.done) break;
                length += part.value.byteLength;
                if (length > CAP || !current()) { await reader.cancel(); throw Error('listing limit'); }
                parts.push(part.value);
            }
            const bytes = new Uint8Array(length); let offset = 0;
            for (const part of parts) { bytes.set(part, offset); offset += part.byteLength; }
            text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
        } finally { reader.releaseLock(); }
    } else text = await response.text();
    if (!current() || encoder.encode(text).length > CAP) throw Error('listing limit');
    return text;
}

export function createAssetInventory({ root, fetch, current = () => true, signal }) {
    const base = inventoryRoot(root);
    async function listing(container) {
        if (!base || !current()) return failed('context_changed');
        try {
            const response = await fetch(container, { headers: { Accept: 'application/ld+json' }, cache: 'no-store', redirect: 'error', signal });
            if (!current()) return failed('context_changed');
            if (response.status === 404) return { ok: true, urls: [], partial: false };
            const json = JSON.parse(await cappedText(response, current));
            const nodes = Array.isArray(json) ? json : (Array.isArray(json?.['@graph']) ? json['@graph'] : [json]);
            const matching = nodes.slice(0, 5000).filter(node => {
                try { return node && new URL(node['@id'] || node.id, container).href === container; } catch { return false; }
            });
            const isContainer = matching.some(node => [node['@type'] || node.type].flat().some(type =>
                ['http://www.w3.org/ns/ldp#BasicContainer', 'http://www.w3.org/ns/ldp#Container', 'ldp:BasicContainer', 'ldp:Container'].includes(type)));
            if (!isContainer) return failed('invalid_listing');
            const urls = parseInboxListing(matching.map(node => ({ contains: node.contains, [CONTAINS_PRED]: node[CONTAINS_PRED] })), container);
            const partial = nodes.length > 5000 || urls.length > 5000 || parseLinkHeader(response.headers?.get('link')).some(link => link.rel?.split(/\s+/).includes('next'));
            return { ok: true, urls: urls.slice(0, 5000), partial };
        } catch { return failed(current() ? 'unavailable' : 'context_changed'); }
    }
    async function rooms() {
        const result = await listing(base);
        if (!result.ok) return result;
        const ids = result.urls.filter(url => url.startsWith(base) && url.endsWith('/') && ROOM.test(url.slice(base.length, -1)))
            .map(url => url.slice(base.length, -1)).sort();
        return { ok: true, rooms: [...new Set(ids)].slice(0, 500), partial: result.partial || ids.length > 500 };
    }
    async function browse(roomId, { path = '', offset = 0, references = [] } = {}) {
        if (!base || typeof roomId !== 'string' || !ROOM.test(roomId) || !validPath(path) || !Number.isInteger(offset) || offset < 0 || offset > 5000 || !current()) return failed('invalid');
        const container = base + roomId + '/' + path, result = await listing(container);
        if (!result.ok) return result;
        const entries = [];
        for (const url of result.urls) {
            if (!url.startsWith(container)) continue;
            const suffix = url.slice(container.length);
            if (path === '') {
                if (CHUNK.test(suffix)) entries.push({ url, name: suffix, kind: 'chunk' });
                else if (suffix === 'files/') entries.push({ url, name: suffix, kind: 'folder', path: 'files/' });
            } else if (path === 'files/') {
                if (suffix.endsWith('/') && ROOM.test(suffix.slice(0, -1))) entries.push({ url, name: suffix, kind: 'folder', path: path + suffix });
                else if (/^[A-Za-z0-9_-]{1,128}\.webm$/.test(suffix)) entries.push({ url, name: suffix, kind: 'voice' });
            } else if (legacyName(suffix)) entries.push({ url, name: suffix, kind: 'legacy' });
        }
        entries.sort((a, b) => a.name.localeCompare(b.name, 'en'));
        const rows = entries.slice(offset, offset + INVENTORY_PAGE), known = new Set(references);
        let partial = result.partial, metadataFailed = false;
        for (const row of rows) {
            if (!current()) return failed('context_changed');
            row.referenced = known.has(row.url);
            if (row.kind === 'folder') continue;
            if (metadataFailed) { row.available = false; row.size = null; continue; }
            try {
                const response = await fetch(row.url, { method: 'HEAD', cache: 'no-store', redirect: 'error', signal });
                if (!current()) return failed('context_changed');
                row.available = response.ok;
                const length = response.headers?.get('content-length');
                row.size = response.ok && typeof length === 'string' && /^\d+$/.test(length) && Number.isSafeInteger(Number(length)) ? Number(length) : null;
                if (!response.ok) partial = true;
                if (response.status >= 500 || [401, 403, 429].includes(response.status)) metadataFailed = true;
            } catch { row.available = false; row.size = null; partial = true; metadataFailed = true; }
        }
        if (!current()) return failed('context_changed');
        return { ok: true, rows, path, offset, next: offset + INVENTORY_PAGE < entries.length ? offset + INVENTORY_PAGE : null, partial };
    }
    return { rooms, browse };
}
