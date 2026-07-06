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

const DB_NAME = 'proxion-dm-history';
const STORE = 'messages';
let _dbPromise = null;

// Retention: keep at most this many messages per thread on this device. Oldest
// beyond the cap are evicted on write so a long-lived device can't grow without
// bound. High enough that normal history is fully retained.
const MAX_PER_THREAD = 2000;

// User switch: "save DM history on this device". Default on; when off,
// dmHistorySave no-ops (nothing new is cached) but existing history still loads
// until explicitly cleared. main.js sets this from localStorage at startup.
let _enabled = true;
export function dmHistorySetEnabled(v) { _enabled = !!v; }
export function dmHistoryEnabled() { return _enabled; }

function _open() {
    if (_dbPromise) return _dbPromise;
    _dbPromise = new Promise((resolve, reject) => {
        if (typeof indexedDB === 'undefined') { reject(new Error('no-indexeddb')); return; }
        const req = indexedDB.open(DB_NAME, 1);
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

// Persist one DM message's PLAINTEXT. `msg` must have message_id, thread_id,
// content (plaintext), from_webid, timestamp; reply_to_id/from_display_name
// optional. Silently no-ops if the record isn't a usable DM message or IDB fails.
export async function dmHistorySave(msg) {
    if (!_enabled) return;
    if (!msg || !msg.message_id || !msg.thread_id) return;
    // Never persist ciphertext or undecryptable placeholders.
    if (msg.e2e) return;
    if (msg.content === '[could not decrypt]' || msg.content === '[decryption error]') return;
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
            });
            tx.oncomplete = resolve;
            tx.onerror = () => reject(tx.error);
        });
        await _enforceCap(db, msg.thread_id);
    } catch (_) { /* best-effort cache */ }
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
        return picked.slice(-maxTotal);  // newest overall if still over budget
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

// Update stored content (on edit).
export async function dmHistoryUpdateContent(messageId, newContent) {
    if (!messageId) return;
    try {
        const db = await _open();
        await new Promise((resolve) => {
            const tx = db.transaction(STORE, 'readwrite');
            const os = tx.objectStore(STORE);
            const g = os.get(messageId);
            g.onsuccess = () => {
                const rec = g.result;
                if (rec) { rec.content = String(newContent); os.put(rec); }
                resolve();
            };
            g.onerror = resolve;
        });
    } catch (_) { /* ignore */ }
}
