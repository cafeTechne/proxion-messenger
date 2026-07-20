// saved.js — R59E: saved messages ("bookmarks"). The GIF-tray idea
// generalized to whole messages: a private, local-only Saved list
// (IndexedDB — nothing leaves the device, zero backend), toggled from a
// bookmark action on any message, browsed in a right panel that mirrors the
// pinned-messages panel.
//
// Reuses the LRU helpers exported by gifs.js (rows carry id/addedAt, so
// evictOverCap/sortByRecency apply as-is).

import { t } from './i18n.js';
import { inlineNotice } from './states.js';
import { evictOverCap, sortByRecency } from './gifs.js';

const DB_NAME = 'proxion-saved-messages';
const STORE = 'saved';
export const MAX_SAVED = 500;

let _dbPromise = null;
function _open() {
    if (_dbPromise) return _dbPromise;
    _dbPromise = new Promise((resolve, reject) => {
        if (typeof indexedDB === 'undefined') { reject(new Error('no-indexeddb')); return; }
        const req = indexedDB.open(DB_NAME, 1);
        req.onupgradeneeded = () => {
            const db = req.result;
            if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: 'id' });
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
    return _dbPromise;
}
function _tx(db, mode) { return db.transaction(STORE, mode).objectStore(STORE); }

export async function listSaved() {
    const db = await _open();
    return new Promise((resolve, reject) => {
        const req = _tx(db, 'readonly').getAll();
        req.onsuccess = () => resolve(sortByRecency(req.result || []));
        req.onerror = () => reject(req.error);
    });
}

// Snapshot in, 'saved' | 'removed' out (toggle by message id).
export async function toggleSaved(snapshot) {
    const db = await _open();
    const existing = await new Promise((res, rej) => {
        const req = _tx(db, 'readonly').get(snapshot.id);
        req.onsuccess = () => res(req.result);
        req.onerror = () => rej(req.error);
    });
    if (existing) {
        await new Promise((res, rej) => {
            const req = _tx(db, 'readwrite').delete(snapshot.id);
            req.onsuccess = () => res();
            req.onerror = () => rej(req.error);
        });
        return 'removed';
    }
    const rows = await listSaved();
    const evict = evictOverCap(rows, MAX_SAVED);
    await new Promise((res, rej) => {
        const store = _tx(db, 'readwrite');
        for (const eid of evict) store.delete(eid);
        store.put({ ...snapshot, addedAt: Date.now() });
        store.transaction.oncomplete = () => res();
        store.transaction.onerror = () => rej(store.transaction.error);
    });
    return 'saved';
}

export async function removeSaved(id) {
    const db = await _open();
    return new Promise((res, rej) => {
        const req = _tx(db, 'readwrite').delete(id);
        req.onsuccess = () => res();
        req.onerror = () => rej(req.error);
    });
}

// Pure: message + view context → the stored snapshot row.
export function snapshotFromMessage(msg, view) {
    const content = (msg.content || '').slice(0, 500);
    return {
        id: msg.message_id,
        thread_id: view?.id || msg.thread_id || '',
        thread_type: view?.type || '',
        thread_label: view?.name || view?.id || '',
        from_name: msg.from_display_name || (msg.from_webid || '').slice(0, 16),
        content,
        has_file: !!msg.file,
        file_kind: msg.file ? (msg.file.mime_type || '').split('/')[0] : '',
        timestamp: msg.timestamp || '',
    };
}

export function createSaved({ showToast, jumpToMsg }) {

    async function showSavedPanel() {
        const panel = document.getElementById('saved-panel');
        if (!panel) return;
        panel.style.display = 'block';
        await renderSaved();
    }

    async function renderSaved() {
        const list = document.getElementById('saved-list');
        if (!list) return;
        let rows = [];
        try { rows = await listSaved(); } catch (_) { /* no IDB */ }
        list.innerHTML = '';
        if (!rows.length) {
            list.innerHTML = inlineNotice(t('saved.none'));
            return;
        }
        for (const row of rows) {
            const div = document.createElement('div');
            div.style.cssText = 'border-bottom:1px solid #334155;padding:8px 0;color:#f1f5f9;';
            const meta = document.createElement('div');
            meta.style.cssText = 'font-size:0.85em;color:#94a3b8;';
            meta.textContent = `${row.from_name} · ${row.thread_label}`;
            const body = document.createElement('div');
            body.style.cssText = 'margin:2px 0;';
            body.textContent = (row.content || (row.has_file ? `[${row.file_kind || 'file'}]` : '')).slice(0, 80);
            const actions = document.createElement('div');
            actions.style.cssText = 'display:flex;gap:8px;margin-top:4px;';
            const jump = document.createElement('button');
            jump.textContent = `[${t('saved.jump')}]`;
            jump.style.cssText = 'background:transparent;border:none;color:#7dd3fc;cursor:pointer;padding:0;font-size:0.8em;';
            jump.addEventListener('click', () => jumpToMsg?.(row.id));
            const rm = document.createElement('button');
            rm.textContent = t('saved.remove');
            rm.style.cssText = 'background:transparent;border:none;color:#94a3b8;cursor:pointer;padding:0;font-size:0.8em;';
            rm.addEventListener('click', async () => {
                await removeSaved(row.id).catch(() => {});
                renderSaved();
            });
            actions.appendChild(jump);
            actions.appendChild(rm);
            div.appendChild(meta);
            div.appendChild(body);
            div.appendChild(actions);
            list.appendChild(div);
        }
    }

    async function toggleBookmark(msg, view) {
        if (!msg?.message_id) return;
        try {
            const r = await toggleSaved(snapshotFromMessage(msg, view));
            showToast(t(r === 'saved' ? 'saved.added' : 'saved.removedToast'));
            if (document.getElementById('saved-panel')?.style.display !== 'none') renderSaved();
        } catch (_) {
            showToast(t('common.updateFailed'));
        }
    }

    function wireSaved() {
        document.getElementById('saved-panel-btn')?.addEventListener('click', showSavedPanel);
        document.getElementById('saved-panel-close')?.addEventListener('click', () => {
            const panel = document.getElementById('saved-panel');
            if (panel) panel.style.display = 'none';
        });
    }

    return { showSavedPanel, renderSaved, toggleBookmark, wireSaved };
}
