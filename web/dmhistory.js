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
import { decodeNativeEdit } from './native-edit-codec.js';
import { nativeMessageRef, validNativeMessageRef, resolveNativeMessageRef } from './native-message-ref.js';

// Base name; the live database is namespaced per authenticated account by
// accountDbName (see auth.js) so one account can never read another's cached DM
// plaintext on a shared device. Pre-namespacing databases (bare DB_NAME) are
// simply never read once an account is signed in — they map to a different name.
const DB_NAME = 'proxion-dm-history';
const STORE = 'messages';
const FILE_STORE = 'native_files';
export const MAX_NATIVE_HISTORY_BYTES = 128 * 1024 * 1024;
export const MAX_NATIVE_HISTORY_FILES = 64;
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
        let blocked = false;
        const req = indexedDB.open(name, 2);
        req.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(STORE)) {
                const os = db.createObjectStore(STORE, { keyPath: 'message_id' });
                os.createIndex('thread_id', 'thread_id', { unique: false });
            }
            if (!db.objectStoreNames.contains(FILE_STORE)) db.createObjectStore(FILE_STORE, { keyPath: 'message_id' }).createIndex('size', 'size');
        };
        req.onblocked = () => { blocked = true; reject(new Error('history-upgrade-blocked')); };
        req.onsuccess = (e) => {
            const db = e.target.result;
            if (blocked) { db.close(); return; }
            db.onversionchange = () => { db.close(); if (_openName === name) _dbPromise = null; };
            resolve(db);
        };
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
    if (typeof file._podHash === 'string' && /^[a-f0-9]{64}$/.test(file._podHash)) out._podHash = file._podHash;
    if (typeof file._nativeAsset === 'string' && file._nativeAsset.length > 0 && file._nativeAsset.length <= 4096) {
        out._nativeAsset = file._nativeAsset;
    } else if (typeof file.data_b64 === 'string' && (file.data_b64 || out.size === 0) && file.data_b64.length <= MAX_FILE_B64) {
        out.data_b64 = file.data_b64;
    } else {
        out.unavailable = true;
    }
    return out;
}

// A record without its file bytes (pure), for the pairing hand-off.
export function stripFileBytes(rec) {
    if (!rec || !rec.file) return rec;
    const { data_b64: _drop, _nativeAsset: _asset, ...meta } = rec.file;
    return { ...rec, file: { ...meta, unavailable: true } };
}

// Apply this device's cached copy of a DM message onto the copy the gateway
// returned in history (pure; mutates `existing`). Returns true when it changed.
export function applyLocalRecord(existing, rec) {
    if (!existing || !rec) return false;
    let changed = false;
    if (rec.sender_verified === true && existing.from_webid === rec.from_webid && !existing.sender_verified) {
        existing.sender_verified = true;
        changed = true;
    }
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
    if (rec.file && existing.from_webid === rec.from_webid && existing.thread_id === rec.thread_id && validNativeMessageRef(rec.native_ref)) {
        for (const field of ['native_ref', 'native_edit_seq', 'native_edit_action', 'native_edit_parent']) {
            if (rec[field] !== undefined && JSON.stringify(existing[field]) !== JSON.stringify(rec[field])) {
                existing[field] = rec[field]; changed = true;
            }
        }
    }
    // The gateway has no DM file bytes; show the copy this device kept.
    if (rec.file && !existing.file?.data_b64 && (rec.file.data_b64 || !existing.file)) {
        existing.file = rec.file;
        changed = true;
    }
    return changed;
}

function _recordForMessage(msg, opts = {}) {
    return {
        message_id: msg.message_id,
        thread_id: msg.thread_id,
        from_webid: msg.from_webid || '',
        from_display_name: msg.from_display_name || '',
        content: msg.content != null ? String(msg.content) : '',
        timestamp: msg.timestamp || new Date().toISOString(),
        reply_to_id: msg.reply_to_id || null,
        ...(msg.sender_verified === true ? { sender_verified: true } : {}),
        ...(msg.edited_at ? { edited_at: msg.edited_at } : {}),
        ...(validNativeMessageRef(msg.native_ref) ? { native_ref: { ...msg.native_ref } } : {}),
        ...(validNativeMessageRef(msg.native_ref) && Number.isSafeInteger(msg.native_edit_seq) && msg.native_edit_seq >= 1 && msg.native_edit_seq <= 100
            && typeof msg.native_edit_action === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(msg.native_edit_action)
            && typeof msg.native_edit_parent === 'string' && /^[a-f0-9]{64}$/.test(msg.native_edit_parent)
            ? { native_edit_seq: msg.native_edit_seq, native_edit_action: msg.native_edit_action, native_edit_parent: msg.native_edit_parent } : {}),
        ...(Array.isArray(msg.edits) && msg.edits.length ? { edits: msg.edits.slice(-MAX_EDITS) } : {}),
        ...(msg.file ? { file: fileForRecord(msg.file) } : {}),
        ...(opts.delivery ? { delivery: opts.delivery } : {}),
        ...(opts.outbox ? { outbox_id: msg.message_id } : {}),
    };
}


// Persist one DM message's PLAINTEXT. `msg` must have message_id, thread_id,
// content (plaintext), from_webid, timestamp; reply_to_id/from_display_name
// optional. Silently no-ops if the record isn't a usable DM message or IDB fails.
// Returns true when there is nothing durable to keep (history off, or an
// intentionally-skipped ciphertext/placeholder) OR the write committed, and
// false only when an enabled write actually failed — so a caller that must not
// discard the source (a consumed pod drop) can tell a real failure from a no-op.
// opts.delivery ('pending') marks our own just-sent DM as not yet delivered; it
// is taken only from this argument, never from the message itself.
export async function dmHistorySave(msg, opts = {}) {
    if (!_enabled) return !opts.requireFile;
    if (!msg || !msg.message_id || !msg.thread_id) return !opts.requireFile;
    // Never persist ciphertext or undecryptable placeholders.
    if (msg.e2e) return !opts.requireFile;
    if (msg.content === '[could not decrypt]' || msg.content === '[decryption error]') return !opts.requireFile;
    const name = accountDbName(DB_NAME);
    const current = () => name === accountDbName(DB_NAME) && (!opts.current || opts.current()) && (!opts.requireFile || _enabled);
    try {
        const db = await _open();
        if (!current()) return false;
        await new Promise((resolve, reject) => {
            const tx = db.transaction(msg.file ? [STORE, FILE_STORE] : STORE, 'readwrite');
            const record = _recordForMessage(msg, opts);
            if (msg.file) {
                const cached = tx.objectStore(FILE_STORE).get(msg.message_id);
                cached.onsuccess = () => {
                    try {
                        if (!current()) { tx.abort(); return; }
                        if (cached.result?.from_webid === msg.from_webid && cached.result.size === record.file?.size) {
                            record.file._nativeAsset = msg.message_id;
                            delete record.file.data_b64; delete record.file.unavailable;
                        }
                        tx.objectStore(STORE).put(record);
                    } catch { try { tx.abort(); } catch { /* Transaction already settled. */ } }
                };
            } else tx.objectStore(STORE).put(record);
            tx.oncomplete = resolve;
            tx.onabort = tx.onerror = () => reject(tx.error);
        });
        await _enforceCap(db, msg.thread_id);
        if (!current()) return false;
        return opts.requireFile ? await _retainedFile(db, msg, current) : true;
    } catch (_) { return false; /* best-effort cache; report failure to callers that care */ }
}

// Native chunk finalization needs actual bytes beyond the ordinary 8 MiB
// base64 retention cap. Commit the Blob and its history reference together.
// A disabled/full/failed store is not a full-file receive commit.
export async function dmHistorySaveFile(msg, blob, { current = () => true } = {}) {
    if (!_enabled || !current() || !(blob instanceof Blob) || blob.size < 1 || blob.size > 25 * 1024 * 1024
        || typeof msg?.message_id !== 'string' || !msg.message_id || msg.message_id.length > 4096
        || typeof msg.thread_id !== 'string' || !msg.thread_id || msg.thread_id.length > 4096
        || typeof msg.from_webid !== 'string' || !msg.from_webid || msg.from_webid.length > 2048
        || msg.e2e === true || !msg.file || msg.file.size !== blob.size) return false;
    const name = accountDbName(DB_NAME), callerCurrent = current;
    current = () => _enabled && callerCurrent() && name === accountDbName(DB_NAME);
    try {
        const bytes = await blob.arrayBuffer();
        if (!current()) return false;
        const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(byte => byte.toString(16).padStart(2, '0')).join('');
        if (!current()) return false;
        const db = await _open();
        if (!current()) return false;
        const saved = await new Promise(resolve => {
            const tx = db.transaction([STORE, FILE_STORE], 'readwrite'), files = tx.objectStore(FILE_STORE);
            let accepted = false;
            tx.oncomplete = () => resolve(accepted && current());
            tx.onabort = tx.onerror = () => resolve(false);
            const safe = work => { try { if (!current()) tx.abort(); else work(); } catch { try { tx.abort(); } catch { /* Transaction already settled. */ } } };
            const prior = files.get(msg.message_id);
            prior.onsuccess = () => safe(() => {
                const existing = prior.result;
                if (existing && (existing.digest !== digest || existing.from_webid !== msg.from_webid || existing.size !== blob.size)) { tx.abort(); return; }
                let size = blob.size, count = 1;
                const cursor = files.index('size').openKeyCursor();
                cursor.onsuccess = () => safe(() => {
                    const row = cursor.result;
                    if (row) {
                        if (row.primaryKey !== msg.message_id) { size += row.key; count++; }
                        if (size > MAX_NATIVE_HISTORY_BYTES || count > MAX_NATIVE_HISTORY_FILES) { tx.abort(); return; }
                        row.continue(); return;
                    }
                    const messages = tx.objectStore(STORE), old = messages.get(msg.message_id);
                    old.onsuccess = () => safe(() => {
                        if (old.result && old.result.from_webid !== msg.from_webid) { tx.abort(); return; }
                        if (!existing) files.put({ message_id: msg.message_id, from_webid: msg.from_webid, size: blob.size, digest, blob });
                        // Retrying a completed file must not overwrite later edits.
                        if (!existing || old.result?.file?._nativeAsset !== msg.message_id) {
                            messages.put(_recordForMessage({ ...msg, file: { ...msg.file, _nativeAsset: msg.message_id } }));
                        }
                        accepted = true;
                    });
                });
            });
        });
        if (!saved || !current()) return false;
        await _enforceCap(db, msg.thread_id);
        return current() && await _retainedFile(db, msg, current, digest);
    } catch { return false; }
}

// Retention or a concurrent clear can remove a just-written message. A native
// file ACK requires the bytes/reference still to exist, not merely a prior put.
async function _retainedFile(db, msg, current, digest = null) {
    return new Promise(resolve => {
        const tx = db.transaction([STORE, FILE_STORE]), record = tx.objectStore(STORE).get(msg.message_id),
            asset = tx.objectStore(FILE_STORE).get(msg.message_id);
        tx.onabort = tx.onerror = () => resolve(false);
        tx.oncomplete = () => {
            const file = record.result?.file, known = asset.result;
            if (!current() || record.result?.from_webid !== msg.from_webid || !file || file.size !== msg.file?.size) { resolve(false); return; }
            resolve(digest ? file._nativeAsset === msg.message_id && known?.digest === digest && known.from_webid === msg.from_webid
                && known.blob instanceof Blob && known.blob.size === msg.file.size
                : typeof file.data_b64 === 'string' && file.data_b64 === msg.file.data_b64);
        };
    });
}

async function _hydrateFile(db, rec, current) {
    if (!rec?.file?._nativeAsset) return rec;
    const unavailable = value => ({ ...value, file: { ...value.file, unavailable: true } });
    try {
        if (!current()) return null;
        const row = await new Promise(resolve => {
            const request = db.transaction(FILE_STORE, 'readonly').objectStore(FILE_STORE).get(rec.file._nativeAsset);
            request.onsuccess = () => resolve(request.result); request.onerror = () => resolve(null);
        });
        if (!current()) return null;
        if (!row || !(row.blob instanceof Blob) || row.blob.size !== row.size || row.message_id !== rec.message_id || row.from_webid !== rec.from_webid || row.size !== rec.file.size) {
            return unavailable(rec);
        }
        const bytes = new Uint8Array(await row.blob.arrayBuffer());
        if (!current()) return null;
        const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(byte => byte.toString(16).padStart(2, '0')).join('');
        if (!current()) return null;
        if (digest !== row.digest) return unavailable(rec);
        const live = await new Promise(resolve => {
            const request = db.transaction(STORE, 'readonly').objectStore(STORE).get(rec.message_id);
            request.onsuccess = () => resolve(request.result); request.onerror = () => resolve(null);
        });
        if (!current() || !live) return null;
        if (live.from_webid !== row.from_webid || live.file?._nativeAsset !== row.message_id || live.file.size !== row.size) return unavailable(live);
        const chunks = [];
        for (let offset = 0; offset < bytes.length; offset += 32768) chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 32768)));
        const file = { ...live.file, data_b64: btoa(chunks.join('')) }; delete file.unavailable;
        return { ...live, file };
    } catch { return current() ? unavailable(rec) : null; }
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
        const tx = db.transaction([STORE, FILE_STORE], 'readwrite');
        const os = tx.objectStore(STORE);
        doomed.forEach((id) => { os.delete(id); tx.objectStore(FILE_STORE).delete(id); });
        tx.oncomplete = resolve;
        tx.onerror = resolve;
    });
}

// Wipe the entire local DM-history store (Settings → "Clear DM history").
export async function dmHistoryClearAll() {
    try {
        const db = await _open();
        await new Promise((resolve) => {
            const tx = db.transaction([STORE, FILE_STORE], 'readwrite');
            tx.objectStore(STORE).clear();
            tx.objectStore(FILE_STORE).clear();
            tx.oncomplete = resolve;
            tx.onerror = resolve;
        });
    } catch (_) { /* ignore */ }
}

// Return stored plaintext messages for a thread, oldest-first. [] on any failure.
export async function dmHistoryLoad(threadId, limit = 500) {
    if (!threadId) return [];
    const name = accountDbName(DB_NAME), current = () => name === accountDbName(DB_NAME);
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
        const selected = limit ? rows.slice(-limit) : rows;
        const hydrated = await Promise.all(selected.map(row => _hydrateFile(db, row, current)));
        return current() ? hydrated.filter(Boolean) : [];
    } catch (_) {
        return [];
    }
}

// This device's cached copy of one DM message, or null.
export async function dmHistoryGet(messageId) {
    if (!messageId) return null;
    const name = accountDbName(DB_NAME), current = () => name === accountDbName(DB_NAME);
    try {
        const db = await _open();
        const rec = await new Promise((resolve) => {
            const req = db.transaction(STORE, 'readonly').objectStore(STORE).get(messageId);
            req.onsuccess = () => resolve(req.result || null);
            req.onerror = () => resolve(null);
        });
        return current() ? await _hydrateFile(db, rec, current) : null;
    } catch (_) {
        return null;
    }
}

// Our own sent DM's delivery state on its cached record: 'pending' or 'failed'
// until the drop is confirmed, then cleared (null). The flag survives a thread
// switch and a reload, so an undelivered message never looks sent. No-op when
// the message is not cached.
export async function dmHistorySetDelivery(messageId, delivery) {
    if (!messageId) return;
    try {
        const db = await _open();
        return await new Promise((resolve) => {
            const tx = db.transaction(STORE, 'readwrite');
            const os = tx.objectStore(STORE);
            const req = os.get(messageId);
            req.onsuccess = () => {
                const rec = req.result;
                if (!rec || (rec.delivery || null) === (delivery || null)) return;
                if (delivery) rec.delivery = delivery; else delete rec.delivery;
                os.put(rec);
            };
            tx.oncomplete = () => resolve(true);
            tx.onerror = tx.onabort = () => resolve(false);
        });
    } catch (_) { return false; }
}

// Cached DMs still flagged undelivered, oldest first.
export async function dmHistoryUndelivered(limit = 200) {
    try {
        const db = await _open();
        const rows = await new Promise((resolve) => {
            const out = [];
            const req = db.transaction(STORE, 'readonly').objectStore(STORE).openCursor();
            req.onsuccess = (e) => {
                const cur = e.target.result;
                if (!cur) { resolve(out); return; }
                if (cur.value && cur.value.delivery) out.push(cur.value);
                cur.continue();
            };
            req.onerror = () => resolve(out);
        });
        rows.sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''));
        return rows.slice(-limit);
    } catch (_) { return []; }
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
            const tx = db.transaction([STORE, FILE_STORE], 'readwrite');
            tx.objectStore(STORE).delete(messageId);
            tx.objectStore(FILE_STORE).delete(messageId);
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
            const tx = db.transaction([STORE, FILE_STORE], 'readwrite');
            const os = tx.objectStore(STORE);
            const req = os.index('thread_id').openCursor(IDBKeyRange.only(threadId));
            req.onsuccess = (e) => {
                const cur = e.target.result;
                if (cur) {
                    if (isExpired(cur.value.timestamp, beforeIso, sinceIso)) { os.delete(cur.primaryKey); tx.objectStore(FILE_STORE).delete(cur.primaryKey); }
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
            const tx = db.transaction([STORE, FILE_STORE], 'readwrite');
            const os = tx.objectStore(STORE);
            const req = os.index('thread_id').openCursor(IDBKeyRange.only(threadId));
            req.onsuccess = (e) => {
                const cur = e.target.result;
                if (cur) { os.delete(cur.primaryKey); tx.objectStore(FILE_STORE).delete(cur.primaryKey); cur.continue(); }
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

// Apply a previously authenticated/decrypted native action. The last action
// identity and caption commit in one transaction, without touching attachment
// bytes. A caller may acknowledge only a true result; missing originals,
// conflicts, disabled history and aborted writes must retain its receive job.
export async function dmHistoryApplyNativeEdit(plaintext, event, context, { current = () => true } = {}) {
    const body = decodeNativeEdit(plaintext, event);
    if (!body || !context || event.from_webid !== context.actorWebId || !_enabled || !current()) return false;
    const name = accountDbName(DB_NAME), callerCurrent = current;
    current = () => _enabled && callerCurrent() && name === accountDbName(DB_NAME);
    const id = `native:${context.actorWebId}:${body.ref.id}`;
    try {
        const db = await _open();
        if (!current()) return false;
        return await new Promise(resolve => {
            const tx = db.transaction(STORE, 'readwrite'), store = tx.objectStore(STORE);
            let accepted = false;
            tx.oncomplete = () => resolve(accepted && current());
            tx.onabort = tx.onerror = () => resolve(false);
            const get = store.get(id);
            get.onsuccess = () => {
                try {
                    if (!current()) { tx.abort(); return; }
                    const rec = resolveNativeMessageRef(body.ref, { ...context, messages: [get.result] });
                    if (!rec) return;
                    const previous = rec.native_edit_seq ?? 0;
                    if (previous === body.seq) {
                        accepted = rec.native_edit_action === body.action_id && rec.native_edit_parent === body.parent && rec.content === body.text;
                        return;
                    }
                    if (!Number.isSafeInteger(previous) || previous < 0 || body.seq !== previous + 1
                        || (previous > 0 && rec.native_edit_parent !== body.parent)) return;
                    applyEditToRecord(rec, body.text, { edited_by: context.actorWebId });
                    rec.native_ref = nativeMessageRef(rec, context);
                    rec.native_edit_seq = body.seq;
                    rec.native_edit_action = body.action_id;
                    rec.native_edit_parent = body.parent;
                    store.put(rec);
                    accepted = true;
                } catch { try { tx.abort(); } catch { /* Already settled. */ } }
            };
        });
    } catch { return false; }
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
