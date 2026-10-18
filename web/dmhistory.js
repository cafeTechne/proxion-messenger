// dmhistory.js — local plaintext persistence of DM messages.
//
// WHY: DMs are E2E-encrypted with a forward Double Ratchet. Message keys are
// consumed as messages are decrypted live, so a message CANNOT be re-decrypted
// from the server's stored ciphertext on reopen — reopening an E2E DM otherwise
// shows ciphertext. (Rooms don't have this problem; they persist plaintext to
// the pod.) Like every ratchet messenger, we keep the DECRYPTED plaintext in a
// local per-device store and render history from it. This also gives multi-device
// its history: each device persists what it itself decrypts/sends.
//
// Scope: DM threads only (rooms use the pod). Keyed by message_id, indexed by
// thread_id. Best-effort — if IndexedDB is unavailable, history just isn't cached
// (same as today) and the app still works live.

import { accountDbName } from './auth.js';

// Base name; the live database is namespaced per authenticated account by
// accountDbName (see auth.js) so one account can never read another's cached DM
// plaintext on a shared device. Pre-namespacing databases (bare DB_NAME) are
// simply never read once an account is signed in — they map to a different name.
const DB_NAME = 'proxion-dm-history';
const STORE = 'messages';
let _dbPromise = null;
let _openName = null;

// Retention: keep at most this many messages per thread on this device. Oldest
// beyond the cap are evicted on write so a long-lived device can't grow without
// bound. High enough that normal history is fully retained.
const MAX_PER_THREAD = 2000;
// Earlier versions kept per message for the edit-history view.
const MAX_EDITS = 50;

// User switch: "save DM history on this device". Default on; when off,
// dmHistorySave no-ops (nothing new is cached) but existing history still loads
// until explicitly cleared. main.js sets this from localStorage at startup.
let _enabled = true;
export function dmHistorySetEnabled(v) { _enabled = !!v; }
export function dmHistoryEnabled() { return _enabled; }

function _open() {
    // Re-derive the account-bound name every open; if the account changed, drop
    // the cached handle so we never serve one account's DB to another.
    const name = accountDbName(DB_NAME);
    if (_dbPromise && _openName === name) return _dbPromise;
    _openName = name;
    _dbPromise = new Promise((resolve, reject) => {
        if (typeof indexedDB === 'undefined') { reject(new Error('no-indexeddb')); return; }
        const req = indexedDB.open(name, 1);
        req.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(STORE)) {
                const os = db.createObjectStore(STORE, { keyPath: 'message_id' });
                os.createIndex('thread_id', 'thread_id', { unique: false });
            }
        };
        req.onsuccess = (e) => resolve(e.target.result);
        req.onerror = (e) => reject(e.target.error);
    }).catch((err) => { _dbPromise = null; throw err; });
    return _dbPromise;
}

// A DM file's decrypted bytes are kept here, on this device only: the gateway
// stores no DM file content (an E2E DM file is ciphertext it cannot read, and
// its history row is just "📎"). Larger files keep only their metadata and
// render as "not available on this device" after a reload.
export const MAX_FILE_B64 = 8 * 1024 * 1024;

// The file fields worth keeping for a history record (pure).
export function fileForRecord(file) {
    if (!file || typeof file !== 'object') return null;
    const out = {
        filename: String(file.filename || 'file'),
        mime_type: String(file.mime_type || 'application/octet-stream'),
        size: Number(file.size) || 0,
    };
    if (Number.isInteger(file.width) && Number.isInteger(file.height)) { out.width = file.width; out.height = file.height; }
    if (file.spoiler === true) out.spoiler = true;
    if (typeof file.data_b64 === 'string' && file.data_b64 && file.data_b64.length <= MAX_FILE_B64) {
        out.data_b64 = file.data_b64;
    } else {
        out.unavailable = true;
    }
    return out;
}

// A record without its file bytes (pure), for the pairing hand-off.
export function stripFileBytes(rec) {
    if (!rec || !rec.file) return rec;
    const { data_b64: _drop, ...meta } = rec.file;
    return { ...rec, file: { ...meta, unavailable: true } };
}

// Apply this device's cached copy of a DM message onto the copy the gateway
// returned in history (pure; mutates `existing`). Returns true when it changed.
export function applyLocalRecord(existing, rec) {
    if (!existing || !rec) return false;
    let changed = false;
    if (existing.content !== rec.content) {
        // Local plaintext is authoritative: the server copy of an E2E DM
        // is ciphertext (stored as a plain string with no e2e flag), so
        // it can't be distinguished from plaintext — always prefer ours.
        existing.content = rec.content;
        existing.e2e = false;
        changed = true;
    }
    // An edit this device saw keeps its marker even when the gateway's row
    // has none (the sender's copy, or a row stored before the edit landed).
    if (rec.edited_at && !existing.edited_at) {
        existing.edited_at = rec.edited_at;
        changed = true;
    }
    if (rec.reply_to_id && !existing.reply_to_id) {
        existing.reply_to_id = rec.reply_to_id;
        changed = true;
    }
    // The gateway has no DM file bytes; show the copy this device kept.
    if (rec.file && !existing.file?.data_b64 && (rec.file.data_b64 || !existing.file)) {
        existing.file = rec.file;
        changed = true;
    }
    return changed;
}

// Persist one DM message's PLAINTEXT. `msg` must have message_id, thread_id,
// content (plaintext), from_webid, timestamp; reply_to_id/from_display_name
// optional. Silently no-ops if the record isn't a usable DM message or IDB fails.
// Returns true when there is nothing durable to keep (history off, or an
// intentionally-skipped ciphertext/placeholder) OR the write committed, and
// false only when an enabled write actually failed — so a caller that must not
// discard the source (a consumed pod drop) can tell a real failure from a no-op.
export async function dmHistorySave(msg) {
    if (!_enabled) return true;
    if (!msg || !msg.message_id || !msg.thread_id) return true;
    // Never persist ciphertext or undecryptable placeholders.
    if (msg.e2e) return true;
    if (msg.content === '[could not decrypt]' || msg.content === '[decryption error]') return true;
    try {
        const db = await _open();
        await new Promise((resolve, reject) => {
            const tx = db.transaction(STORE, 'readwrite');
            tx.objectStore(STORE).put({
                message_id: msg.message_id,
                thread_id: msg.thread_id,
                from_webid: msg.from_webid || '',
                from_display_name: msg.from_display_name || '',
                content: msg.content != null ? String(msg.content) : '',
                timestamp: msg.timestamp || new Date().toISOString(),
                reply_to_id: msg.reply_to_id || null,
                ...(msg.edited_at ? { edited_at: msg.edited_at } : {}),
                ...(Array.isArray(msg.edits) && msg.edits.length ? { edits: msg.edits.slice(-MAX_EDITS) } : {}),
                ...(msg.file ? { file: fileForRecord(msg.file) } : {}),
            });
            tx.oncomplete = resolve;
            tx.onerror = () => reject(tx.error);
        });
        await _enforceCap(db, msg.thread_id);
        return true;
    } catch (_) { return false; /* best-effort cache; report failure to callers that care */ }
}

// Given all rows for a thread and a cap, return the message_ids to evict
// (the oldest, keeping the newest `cap`). Pure — unit-tested directly.
export function planEviction(rows, cap = MAX_PER_THREAD) {
    if (!Array.isArray(rows) || rows.length <= cap) return [];
    const sorted = rows.slice().sort((a, b) =>
        (a.timestamp || '').localeCompare(b.timestamp || ''));
    return sorted.slice(0, rows.length - cap).map((r) => r.message_id);
}

// Trim a thread to MAX_PER_THREAD. Uses a cheap index count first so the O(n)
// scan+delete only runs when actually over the cap.
async function _enforceCap(db, threadId) {
    const count = await new Promise((resolve) => {
        const tx = db.transaction(STORE, 'readonly');
        const req = tx.objectStore(STORE).index('thread_id').count(IDBKeyRange.only(threadId));
        req.onsuccess = () => resolve(req.result || 0);
        req.onerror = () => resolve(0);
    });
    if (count <= MAX_PER_THREAD) return;
    const rows = await new Promise((resolve) => {
        const out = [];
        const tx = db.transaction(STORE, 'readonly');
        const req = tx.objectStore(STORE).index('thread_id').openCursor(IDBKeyRange.only(threadId));
        req.onsuccess = (e) => { const c = e.target.result; if (c) { out.push(c.value); c.continue(); } else resolve(out); };
        req.onerror = () => resolve(out);
    });
    const doomed = planEviction(rows, MAX_PER_THREAD);
    if (!doomed.length) return;
    await new Promise((resolve) => {
        const tx = db.transaction(STORE, 'readwrite');
        const os = tx.objectStore(STORE);
        doomed.forEach((id) => os.delete(id));
        tx.oncomplete = resolve;
        tx.onerror = resolve;
    });
}

// Wipe the entire local DM-history store (Settings → "Clear DM history").
export async function dmHistoryClearAll() {
    try {
        const db = await _open();
        await new Promise((resolve) => {
            const tx = db.transaction(STORE, 'readwrite');
            tx.objectStore(STORE).clear();
            tx.oncomplete = resolve;
            tx.onerror = resolve;
        });
    } catch (_) { /* ignore */ }
}

// Return stored plaintext messages for a thread, oldest-first. [] on any failure.
export async function dmHistoryLoad(threadId, limit = 500) {
    if (!threadId) return [];
    try {
        const db = await _open();
        const rows = await new Promise((resolve, reject) => {
            const out = [];
            const tx = db.transaction(STORE, 'readonly');
            const idx = tx.objectStore(STORE).index('thread_id');
            const req = idx.openCursor(IDBKeyRange.only(threadId));
            req.onsuccess = (e) => {
                const cur = e.target.result;
                if (cur) { out.push(cur.value); cur.continue(); }
                else resolve(out);
            };
            req.onerror = () => reject(req.error);
        });
        rows.sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''));
        return limit ? rows.slice(-limit) : rows;
    } catch (_) {
        return [];
    }
}

// This device's cached copy of one DM message, or null.
export async function dmHistoryGet(messageId) {
    if (!messageId) return null;
    try {
        const db = await _open();
        return await new Promise((resolve) => {
            const req = db.transaction(STORE, 'readonly').objectStore(STORE).get(messageId);
            req.onsuccess = () => resolve(req.result || null);
            req.onerror = () => resolve(null);
        });
    } catch (_) {
        return null;
    }
}

// True when every word of `query` appears in `text`, ignoring case (pure).
// This is how the cached DM plaintext is searched: the gateway only holds
// ciphertext for E2E DMs, so it cannot.
export function matchesQuery(text, query) {
    const words = String(query || '').toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return false;
    const hay = String(text || '').toLowerCase();
    return words.every((w) => hay.includes(w));
}

// Cached DM messages matching `query`, newest first, without file bytes.
export async function dmHistorySearch(query, limit = 50) {
    if (!String(query || '').trim()) return [];
    try {
        const db = await _open();
        const rows = await new Promise((resolve) => {
            const out = [];
            const req = db.transaction(STORE, 'readonly').objectStore(STORE).openCursor();
            req.onsuccess = (e) => {
                const c = e.target.result;
                if (!c) { resolve(out); return; }
                if (matchesQuery(c.value.content, query)) out.push(c.value);
                c.continue();
            };
            req.onerror = () => resolve(out);
        });
        rows.sort((a, b) => (b.timestamp || '').localeCompare(a.timestamp || ''));
        return rows.slice(0, limit).map(stripFileBytes);
    } catch (_) {
        return [];
    }
}

// Remove one message (on delete) — keep the local cache consistent.
export async function dmHistoryDelete(messageId) {
    if (!messageId) return;
    try {
        const db = await _open();
        await new Promise((resolve) => {
            const tx = db.transaction(STORE, 'readwrite');
            tx.objectStore(STORE).delete(messageId);
            tx.oncomplete = resolve;
            tx.onerror = resolve;
        });
    } catch (_) { /* ignore */ }
}

// Remove a thread's cached messages older than a cutoff — disappearing-message
// expiry. Without this, "disappeared" DMs lived on in the local plaintext cache
// and re-rendered on the next open (the merge is local-always-wins). With
// sinceIso, messages sent before the timer was turned on are kept.
export function isExpired(timestamp, beforeIso, sinceIso = '') {
    const ts = timestamp || '';
    return ts < beforeIso && (!sinceIso || ts >= sinceIso);
}

export async function dmHistoryDeleteBefore(threadId, beforeIso, sinceIso = '') {
    if (!threadId || !beforeIso) return;
    try {
        const db = await _open();
        await new Promise((resolve) => {
            const tx = db.transaction(STORE, 'readwrite');
            const os = tx.objectStore(STORE);
            const req = os.index('thread_id').openCursor(IDBKeyRange.only(threadId));
            req.onsuccess = (e) => {
                const cur = e.target.result;
                if (cur) {
                    if (isExpired(cur.value.timestamp, beforeIso, sinceIso)) os.delete(cur.primaryKey);
                    cur.continue();
                }
            };
            tx.oncomplete = resolve;
            tx.onerror = resolve;
        });
    } catch (_) { /* ignore */ }
}

// Remove ALL cached messages for a thread — on contact removal/revoke/block, so
// a removed contact's plaintext DMs don't linger locally.
export async function dmHistoryDeleteThread(threadId) {
    if (!threadId) return;
    try {
        const db = await _open();
        await new Promise((resolve) => {
            const tx = db.transaction(STORE, 'readwrite');
            const os = tx.objectStore(STORE);
            const req = os.index('thread_id').openCursor(IDBKeyRange.only(threadId));
            req.onsuccess = (e) => {
                const cur = e.target.result;
                if (cur) { os.delete(cur.primaryKey); cur.continue(); }
            };
            tx.oncomplete = resolve;
            tx.onerror = resolve;
        });
    } catch (_) { /* ignore */ }
}

// Export the most recent messages across all threads, for handing to a newly
// paired device over the (safety-code-authenticated) pairing channel so it
// starts with history instead of a blank slate. Bounded so it fits one relay
// message. Returns a plain array of records (newest-biased, chronological).
export async function dmHistoryExportRecent(maxPerThread = 40, maxTotal = 400) {
    try {
        const db = await _open();
        const rows = await new Promise((resolve) => {
            const out = [];
            const tx = db.transaction(STORE, 'readonly');
            const req = tx.objectStore(STORE).openCursor();
            req.onsuccess = (e) => { const c = e.target.result; if (c) { out.push(c.value); c.continue(); } else resolve(out); };
            req.onerror = () => resolve(out);
        });
        const byThread = new Map();
        for (const r of rows) {
            const arr = byThread.get(r.thread_id) || [];
            arr.push(r); byThread.set(r.thread_id, arr);
        }
        let picked = [];
        for (const arr of byThread.values()) {
            arr.sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''));
            picked = picked.concat(arr.slice(-maxPerThread));  // newest per thread
        }
        picked.sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''));
        // File bytes would blow the one-message budget: the new device gets the
        // file's name and an explicit "not available on this device" state.
        return picked.slice(-maxTotal).map(stripFileBytes);  // newest overall if still over budget
    } catch (_) {
        return [];
    }
}

// Import records handed over at pairing time. Honors the enable switch and the
// per-thread cap (reuses dmHistorySave). Returns the count imported.
export async function dmHistoryImport(records) {
    if (!_enabled) return 0;
    if (!Array.isArray(records) || !records.length) return 0;
    let n = 0;
    for (const r of records) {
        if (!r || !r.message_id || !r.thread_id) continue;
        await dmHistorySave({ ...r, e2e: false });
        n++;
    }
    return n;
}

// Pure: apply an edit to a stored record. With `meta` ({edited_at,
// edited_by}) the previous text is kept in rec.edits for the edit-history view,
// which for an E2E DM cannot come from the gateway (it holds ciphertext).
export function applyEditToRecord(rec, newContent, meta) {
    const next = String(newContent);
    if (meta && rec.content !== next) {
        const edits = Array.isArray(rec.edits) ? rec.edits.slice() : [];
        edits.push({ prev_content: rec.content, edited_at: meta.edited_at || new Date().toISOString(), edited_by: meta.edited_by || '' });
        rec.edits = edits.slice(-MAX_EDITS);
        rec.edited_at = meta.edited_at || new Date().toISOString();
    }
    rec.content = next;
    return rec;
}

// Earlier versions of one message, oldest first ([] when none are cached).
export async function dmHistoryGetEdits(messageId) {
    if (!messageId) return [];
    try {
        const db = await _open();
        const rec = await new Promise((resolve) => {
            const tx = db.transaction(STORE, 'readonly');
            const g = tx.objectStore(STORE).get(messageId);
            g.onsuccess = () => resolve(g.result || null);
            g.onerror = () => resolve(null);
        });
        return rec && Array.isArray(rec.edits) ? rec.edits : [];
    } catch (_) { return []; }
}

// Update stored content (on edit).
export async function dmHistoryUpdateContent(messageId, newContent, meta = null) {
    if (!messageId) return;
    try {
        const db = await _open();
        await new Promise((resolve) => {
            const tx = db.transaction(STORE, 'readwrite');
            const os = tx.objectStore(STORE);
            const g = os.get(messageId);
            g.onsuccess = () => {
                const rec = g.result;
                if (rec) { os.put(applyEditToRecord(rec, newContent, meta)); }
                resolve();
            };
            g.onerror = resolve;
        });
    } catch (_) { /* ignore */ }
}
