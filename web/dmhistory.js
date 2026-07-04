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
    } catch (_) { /* best-effort cache */ }
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
