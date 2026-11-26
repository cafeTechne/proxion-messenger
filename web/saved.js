// saved.js — R59E: saved messages ("bookmarks"). The GIF-tray idea
// generalized to whole messages: a private, local-only Saved list
// (IndexedDB — nothing leaves the device, zero backend), toggled from a
// bookmark action on any message, browsed in a right panel that mirrors the
// pinned-messages panel.
//
// Reuses the LRU helpers exported by gifs.js (rows carry id/addedAt, so
// evictOverCap/sortByRecency apply as-is).

import { t } from './i18n.js';
import { parseRoomAttachment } from './room-attachments.js';
import { parseDmAttachment } from './dm-attachments.js';
import { inlineNotice } from './states.js';
import { evictOverCap, sortByRecency } from './gifs.js';
import { podSyncSavedMessage, podSyncRemoveSavedMessage, podReadSavedMessages } from './pod.js';
import { accountDbName } from './auth.js';
import { isExpired } from './dmhistory.js';

// Base name; the live database is namespaced per authenticated account by
// accountDbName (see auth.js), because a snapshot stores up to 500 chars of a
// message's decrypted content and must never be read by a different account on a
// shared device. Pre-namespacing databases map to a different name and are never
// read once an account is signed in.
const DB_NAME = 'proxion-saved-messages';
const STORE = 'saved';
export const MAX_SAVED = 500;

let _dbPromise = null;
let _openName = null;
function _open() {
    // Re-derive the account-bound name every open; drop the cached handle if the
    // account changed so one account never serves another its saved snapshots.
    const name = accountDbName(DB_NAME);
    if (_dbPromise && _openName === name) return _dbPromise;
    _openName = name;
    _dbPromise = new Promise((resolve, reject) => {
        if (typeof indexedDB === 'undefined') { reject(new Error('no-indexeddb')); return; }
        const req = indexedDB.open(name, 1);
        req.onupgradeneeded = () => {
            const db = req.result;
            if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: 'id' });
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
    return _dbPromise;
}
function accountContext() {
    const name = accountDbName(DB_NAME);
    return { current: () => name === accountDbName(DB_NAME) };
}
function snapshotRow(row) {
    if (!row || typeof row.id !== 'string' || !row.id || row.id.length > 4096) throw Error('Invalid saved reference');
    const text = (value, limit) => typeof value === 'string' ? value.slice(0, limit) : '';
    return { id: row.id, thread_id: text(row.thread_id, 4096), thread_type: text(row.thread_type, 32),
        thread_label: text(row.thread_label, 256), from_name: text(row.from_name, 256), content: text(row.content, 500),
        has_file: row.has_file === true, file_kind: text(row.file_kind, 32), timestamp: text(row.timestamp, 64),
        ...(typeof row.reference_target === 'string' ? { reference_target: row.reference_target.slice(0, 4096) } : {}),
        addedAt: Number.isFinite(row.addedAt) ? row.addedAt : Date.now() };
}
async function transaction(mode, context, operation) {
    const db = await _open();
    if (!context.current()) throw Error('Saved account changed');
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, mode), store = tx.objectStore(STORE);
        let result;
        const safe = fn => { try { if (!context.current()) tx.abort(); else fn(); } catch { try { tx.abort(); } catch { /* settled */ } } };
        tx.oncomplete = () => context.current() ? resolve(result) : reject(Error('Saved account changed'));
        tx.onabort = tx.onerror = () => reject(tx.error || Error('Saved transaction aborted'));
        try { operation(store, value => { result = value; }, safe); } catch (error) { tx.abort(); reject(error); }
    });
}
export async function listSaved() {
    const context = accountContext();
    try { return await transaction('readonly', context, (store, finish, safe) => {
        const request = store.getAll(); request.onsuccess = () => safe(() => finish(sortByRecency(request.result || [])));
    }); } catch { if (!context.current()) return []; throw Error('Saved history unavailable'); }
}
async function putSaved(snapshot, toggle, context = accountContext()) {
    const row = snapshotRow(snapshot);
    return transaction('readwrite', context, (store, finish, safe) => {
        const existing = store.get(row.id);
        existing.onsuccess = () => safe(() => {
            if (existing.result) {
                if (toggle) store.delete(row.id);
                finish(toggle ? 'removed' : false); return;
            }
            const all = store.getAll(); all.onsuccess = () => safe(() => {
                for (const id of evictOverCap(all.result, MAX_SAVED)) store.delete(id);
                store.put(row); finish(toggle ? 'saved' : true);
            });
        });
    });
}
export function toggleSaved(snapshot) { return putSaved(snapshot, true); }
export function removeSaved(id, context = accountContext()) {
    return transaction('readwrite', context, (store, finish, safe) => {
        safe(() => { store.delete(id); finish(true); });
    });
}

// Bind each multi-await operation to its original account and transaction.
export async function forgetSavedMessage(id) {
    if (!id) return false;
    const context = accountContext();
    const removed = await transaction('readwrite', context, (store, finish, safe) => {
        const existing = store.get(id); existing.onsuccess = () => safe(() => {
            if (existing.result) store.delete(id); finish(!!existing.result);
        });
    });
    if (removed && context.current()) await podSyncRemoveSavedMessage(id).catch(() => {});
    return context.current() && removed;
}
export async function forgetSavedExpired(threadIds, beforeIso, sinceIso = '') {
    const context = accountContext(), ids = new Set((threadIds || []).filter(Boolean));
    if (!ids.size || !beforeIso) return 0;
    const victims = (await listSaved()).filter(row => ids.has(row.thread_id) && row.timestamp && isExpired(row.timestamp, beforeIso, sinceIso));
    if (!context.current()) return 0;
    for (const row of victims) {
        if (!context.current()) return 0;
        await removeSaved(row.id, context);
        if (context.current()) await podSyncRemoveSavedMessage(row.id).catch(() => {});
    }
    return context.current() ? victims.length : 0;
}
export function upsertSaved(row, context = accountContext()) { return putSaved(row, false, context); }
export async function pushAllSavedToPod() {
    const context = accountContext(); let rows;
    try { rows = await listSaved(); } catch { return; }
    for (const row of rows) {
        if (!context.current()) return;
        try { await podSyncSavedMessage(row); } catch { /* skip */ }
    }
}
export async function syncSavedFromPod() {
    const context = accountContext(); let rows;
    try { rows = await podReadSavedMessages(); } catch { return 0; }
    if (!context.current()) return 0;
    let added = 0;
    for (const row of rows.slice(0, MAX_SAVED)) {
        if (!context.current()) return 0;
        try { if (await upsertSaved(row, context)) added++; } catch { /* skip invalid references */ }
    }
    return context.current() ? added : 0;
}

// Pure: message + view context → the stored snapshot row.
export function snapshotFromMessage(msg, view) {
    const content = ((parseRoomAttachment(msg.content) || parseDmAttachment(msg.content))?.name || msg.content || '').slice(0, 500);
    return {
        id: msg.message_id,
        thread_id: view?.id || msg.thread_id || '',
        thread_type: view?.type || '',
        thread_label: view?.name || view?.id || '',
        from_name: msg.from_display_name || (msg.from_webid || '').slice(0, 16),
        content,
        has_file: !!msg.file,
        file_kind: msg.file ? (msg.file.mime_type || '').split('/')[0] : '',
        timestamp: msg.pod_ts || msg.timestamp || '',
        ...(typeof msg.iri === 'string' && msg.iri.length <= 4096 ? { reference_target: msg.iri } : {}),
    };
}

export function createSaved({ showToast, jumpToMsg, navigateReference = null, getScope = () => accountDbName(DB_NAME) }) {
    let epoch = 0;
    function reset() {
        epoch++;
        const list = document.getElementById('saved-list'), panel = document.getElementById('saved-panel');
        if (list) list.innerHTML = '';
        if (panel) panel.style.display = 'none';
    }

    async function showSavedPanel() {
        const panel = document.getElementById('saved-panel');
        if (!panel) return;
        panel.style.display = 'block';
        const scope = getScope(), stamp = epoch;
        await renderSaved();
        if (scope !== getScope() || stamp !== epoch) return;
        // R62: pull any pod-synced bookmarks from other devices, then re-render
        // if anything new arrived.
        syncSavedFromPod().then((n) => { if (n > 0 && scope === getScope() && stamp === epoch) renderSaved(); }).catch(() => {});
    }

    async function renderSaved() {
        const list = document.getElementById('saved-list');
        if (!list) return;
        const scope = getScope(), stamp = epoch;
        const current = () => scope === getScope() && stamp === epoch;
        let rows = [];
        try { rows = await listSaved(); } catch (_) {
            if (current()) list.innerHTML = inlineNotice(t('common.updateFailed'));
            return;
        }
        if (!current()) { if (stamp === epoch) reset(); return; }
        list.innerHTML = '';
        if (!rows.length) {
            list.innerHTML = inlineNotice(t('saved.none'));
            return;
        }
        for (const row of rows) {
            const div = document.createElement('div');
            div.className = 'ref-row';
            const meta = document.createElement('div');
            meta.className = 'ref-row__meta';
            meta.textContent = `${row.from_name} · ${row.thread_label}`;
            const body = document.createElement('div');
            body.className = 'ref-row__body';
            body.textContent = (row.content || (row.has_file ? `[${row.file_kind || 'file'}]` : '')).slice(0, 80);
            const actions = document.createElement('div');
            actions.className = 'ref-row__actions';
            const jump = document.createElement('button');
            jump.textContent = `[${t('saved.jump')}]`;
            jump.className = 'link-btn link-btn--info';
            jump.addEventListener('click', async () => {
                if (!current()) { reset(); return; }
                if (navigateReference) await navigateReference({ message_id: row.id, thread_id: row.thread_id,
                    thread_type: row.thread_type, timestamp: row.timestamp, reference_target: row.reference_target });
                else jumpToMsg?.(row.id);
            });
            const rm = document.createElement('button');
            rm.textContent = t('saved.remove');
            rm.className = 'link-btn';
            rm.addEventListener('click', async () => {
                if (!current()) { reset(); return; }
                await removeSaved(row.id).catch(() => {});
                if (!current()) { reset(); return; }
                podSyncRemoveSavedMessage(row.id).catch(() => {});   // R62
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
        const scope = getScope(), stamp = epoch;
        const current = () => scope === getScope() && stamp === epoch;
        try {
            const snapshot = snapshotFromMessage(msg, view);
            const r = await toggleSaved(snapshot);
            if (!current()) { reset(); return; }
            // R62: mirror the change to the pod (no-op unless sync opted in)
            if (r === 'saved') podSyncSavedMessage({ ...snapshot, addedAt: Date.now() }).catch(() => {});
            else podSyncRemoveSavedMessage(snapshot.id).catch(() => {});
            showToast(t(r === 'saved' ? 'saved.added' : 'saved.removedToast'));
            if (document.getElementById('saved-panel')?.style.display !== 'none') renderSaved();
        } catch (_) {
            if (current()) showToast(t('common.updateFailed'));
        }
    }

    function wireSaved() {
        document.getElementById('saved-panel-btn')?.addEventListener('click', showSavedPanel);
        document.getElementById('saved-panel-close')?.addEventListener('click', () => {
            reset();
        });
    }

    return { showSavedPanel, renderSaved, toggleBookmark, wireSaved, reset };
}
