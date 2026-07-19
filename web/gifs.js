// gifs.js — personal GIF/meme tray (R58). Star any image attachment in the
// feed into a local IndexedDB library and re-send it from a composer panel.
// Sovereign by design: no Giphy/Tenor — the library is exactly what the user
// has starred, it never leaves the device, and re-sends go through the normal
// E2E attachment path.
//
// Factory like the other modules: host helpers are injected, wireGifTray()
// attaches listeners. Pure helpers (evictOverCap, contentId, fileFromFavorite)
// are exported separately for tests.

import { t } from './i18n.js';

const DB_NAME = 'proxion-gif-tray';
const STORE = 'favorites';
export const MAX_FAVORITES = 200;

let _dbPromise = null;
function _open() {
    if (_dbPromise) return _dbPromise;
    _dbPromise = new Promise((resolve, reject) => {
        if (typeof indexedDB === 'undefined') { reject(new Error('no-indexeddb')); return; }
        const req = indexedDB.open(DB_NAME, 1);
        req.onupgradeneeded = () => {
            const db = req.result;
            if (!db.objectStoreNames.contains(STORE)) {
                db.createObjectStore(STORE, { keyPath: 'id' });
            }
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
    return _dbPromise;
}

function _tx(db, mode) {
    return db.transaction(STORE, mode).objectStore(STORE);
}

// Content-addressed id: sha256 of the base64 payload, so the same image
// starred twice (even from different messages) dedupes to one entry.
export async function contentId(dataB64) {
    const bytes = new TextEncoder().encode(dataB64 || '');
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
}

// Pure: which ids to delete so that adding one more stays within cap.
// Evicts least-recently-used (lastUsedAt, falling back to addedAt).
export function evictOverCap(rows, cap) {
    if (!Array.isArray(rows) || rows.length < cap) return [];
    const sorted = [...rows].sort(
        (a, b) => (a.lastUsedAt || a.addedAt || 0) - (b.lastUsedAt || b.addedAt || 0));
    return sorted.slice(0, rows.length - cap + 1).map(r => r.id);
}

export function fileFromFavorite(row) {
    const bin = atob(row.data_b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new File([bytes], row.filename || 'image', { type: row.mime });
}

// Pure: most-recently-used first (new, never-sent stars count as "used" at
// their save time); useCount then id break ties deterministically.
export function sortByRecency(rows) {
    return [...rows].sort((a, b) =>
        ((b.lastUsedAt || b.addedAt || 0) - (a.lastUsedAt || a.addedAt || 0)) ||
        ((b.useCount || 0) - (a.useCount || 0)) ||
        String(a.id).localeCompare(String(b.id)));
}

export async function listFavorites() {
    const db = await _open();
    return new Promise((resolve, reject) => {
        const req = _tx(db, 'readonly').getAll();
        req.onsuccess = () => resolve(sortByRecency(req.result || []));
        req.onerror = () => reject(req.error);
    });
}

// Returns 'saved' or 'exists'.
export async function saveFavorite({ filename, mime, data_b64 }) {
    const id = await contentId(data_b64);
    const db = await _open();
    const existing = await new Promise((res, rej) => {
        const req = _tx(db, 'readonly').get(id);
        req.onsuccess = () => res(req.result);
        req.onerror = () => rej(req.error);
    });
    if (existing) return 'exists';
    const rows = await listFavorites();
    const evict = evictOverCap(rows, MAX_FAVORITES);
    await new Promise((res, rej) => {
        const store = _tx(db, 'readwrite');
        for (const eid of evict) store.delete(eid);
        store.put({ id, filename: filename || 'image', mime, data_b64, addedAt: Date.now(), lastUsedAt: 0, useCount: 0 });
        store.transaction.oncomplete = () => res();
        store.transaction.onerror = () => rej(store.transaction.error);
    });
    return 'saved';
}

export async function removeFavorite(id) {
    const db = await _open();
    return new Promise((res, rej) => {
        const req = _tx(db, 'readwrite').delete(id);
        req.onsuccess = () => res();
        req.onerror = () => rej(req.error);
    });
}

export async function touchFavorite(id) {
    const db = await _open();
    const row = await new Promise((res, rej) => {
        const req = _tx(db, 'readonly').get(id);
        req.onsuccess = () => res(req.result);
        req.onerror = () => rej(req.error);
    });
    if (!row) return;
    row.lastUsedAt = Date.now();
    row.useCount = (row.useCount || 0) + 1;
    await new Promise((res, rej) => {
        const req = _tx(db, 'readwrite').put(row);
        req.onsuccess = () => res();
        req.onerror = () => rej(req.error);
    });
}

export function createGifTray({ showToast, sendAttachmentFile }) {
    let _open_ = false;

    function _panel() { return document.getElementById('gif-tray'); }

    async function _render() {
        const panel = _panel();
        if (!panel) return;
        const grid = panel.querySelector('#gif-tray-grid');
        if (!grid) return;
        grid.innerHTML = '';
        let rows = [];
        try { rows = await listFavorites(); } catch (_) { /* no IDB — empty tray */ }
        if (!rows.length) {
            const p = document.createElement('p');
            p.className = 'gif-tray-empty';
            p.textContent = t('gif.empty');
            grid.appendChild(p);
            return;
        }
        for (const row of rows) {
            const cell = document.createElement('div');
            cell.className = 'gif-cell';
            const sendBtn = document.createElement('button');
            sendBtn.type = 'button';
            sendBtn.className = 'gif-send';
            sendBtn.setAttribute('aria-label', t('gif.sendNamed', { name: row.filename }));
            const img = document.createElement('img');
            img.src = `data:${row.mime};base64,${row.data_b64}`;
            img.alt = '';
            img.loading = 'lazy';
            sendBtn.appendChild(img);
            sendBtn.addEventListener('click', async () => {
                closeTray();
                try {
                    await sendAttachmentFile(fileFromFavorite(row));
                    touchFavorite(row.id).catch(() => {});
                } catch (_) {
                    showToast(t('file.sendFailed'));
                }
            });
            const rmBtn = document.createElement('button');
            rmBtn.type = 'button';
            rmBtn.className = 'gif-remove';
            rmBtn.textContent = '×';
            rmBtn.setAttribute('aria-label', t('gif.removeNamed', { name: row.filename }));
            rmBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                await removeFavorite(row.id).catch(() => {});
                _render();
            });
            cell.appendChild(sendBtn);
            cell.appendChild(rmBtn);
            grid.appendChild(cell);
        }
    }

    function openTray() {
        const panel = _panel();
        const btn = document.getElementById('gif-tray-btn');
        if (!panel) return;
        _open_ = true;
        panel.style.display = 'block';
        btn?.setAttribute('aria-expanded', 'true');
        _render().then(() => {
            panel.querySelector('.gif-send, .gif-tray-empty')?.focus?.();
        });
    }

    function closeTray() {
        const panel = _panel();
        if (!panel) return;
        _open_ = false;
        panel.style.display = 'none';
        document.getElementById('gif-tray-btn')?.setAttribute('aria-expanded', 'false');
    }

    function toggleTray() { _open_ ? closeTray() : openTray(); }

    function wireGifTray() {
        document.getElementById('gif-tray-btn')?.addEventListener('click', toggleTray);
        const panel = _panel();
        panel?.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                e.stopPropagation();
                closeTray();
                document.getElementById('gif-tray-btn')?.focus();
            }
        });
        // Close on outside click (same pattern as the profile card).
        document.addEventListener('click', (e) => {
            if (!_open_) return;
            if (!_panel()?.contains(e.target) && !e.target.closest('#gif-tray-btn')) closeTray();
        });
    }

    return { openTray, closeTray, toggleTray, wireGifTray, _render };
}
