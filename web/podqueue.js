// podqueue.js — durable offline queue for room pod writes (PLAN_ROUND_69 D3).
//
// D2 made a room send write-through: it is not durably done until its pod write
// lands. But a write can fail because the device is offline or the pod is briefly
// unreachable, and a page reload would then lose it. This persists a FAILED room
// pod write in IndexedDB, keyed by message_id (so replays dedup by id), and
// flushes the backlog in send order when connectivity returns. A queued write is
// removed the moment its write finally succeeds.
//
// Scope: room messages only. DMs are E2E, cached by dmhistory.js, and are not
// Long-Chat-mirrored. Best-effort: if IndexedDB is unavailable the queue simply
// does nothing and D2's in-session retry note still covers the current session.

const DB_NAME = 'proxion-pod-queue';
const STORE = 'writes';
let _dbPromise = null;

function _open() {
    if (_dbPromise) return _dbPromise;
    _dbPromise = new Promise((resolve, reject) => {
        if (typeof indexedDB === 'undefined') { reject(new Error('no-indexeddb')); return; }
        const req = indexedDB.open(DB_NAME, 1);
        req.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(STORE)) {
                db.createObjectStore(STORE, { keyPath: 'message_id' });
            }
        };
        req.onsuccess = (e) => resolve(e.target.result);
        req.onerror = (e) => reject(e.target.error);
    }).catch((err) => { _dbPromise = null; throw err; });
    return _dbPromise;
}

// Enqueue (or refresh) a pending room pod write. Keyed by message_id, so the same
// message queued twice stays ONE entry (dedup by id). `queued_at` preserves send
// order for an ordered replay.
export async function podQueueAdd(entry) {
    if (!entry || !entry.message_id || !entry.room_id) return;
    try {
        const db = await _open();
        await new Promise((resolve, reject) => {
            const tx = db.transaction(STORE, 'readwrite');
            tx.objectStore(STORE).put({
                message_id: entry.message_id,
                room_id: entry.room_id,
                msg: entry.msg || {},
                queued_at: entry.queued_at || Date.now(),
            });
            tx.oncomplete = resolve;
            tx.onerror = () => reject(tx.error);
        });
    } catch (_) { /* best-effort */ }
}

export async function podQueueRemove(messageId) {
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

// All queued writes, oldest-first (send order).
export async function podQueueList() {
    try {
        const db = await _open();
        const rows = await new Promise((resolve) => {
            const out = [];
            const tx = db.transaction(STORE, 'readonly');
            const req = tx.objectStore(STORE).openCursor();
            req.onsuccess = (e) => { const c = e.target.result; if (c) { out.push(c.value); c.continue(); } else resolve(out); };
            req.onerror = () => resolve(out);
        });
        rows.sort((a, b) => (a.queued_at || 0) - (b.queued_at || 0));
        return rows;
    } catch (_) { return []; }
}

export async function podQueueCount() {
    try {
        const db = await _open();
        return await new Promise((resolve) => {
            const tx = db.transaction(STORE, 'readonly');
            const req = tx.objectStore(STORE).count();
            req.onsuccess = () => resolve(req.result || 0);
            req.onerror = () => resolve(0);
        });
    } catch (_) { return 0; }
}

export async function podQueueClear() {
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

// Replay the backlog in send order. `writeFn(entry)` performs the pod write and
// resolves truthy on success; a successful entry is removed, and replay STOPS at
// the first failure so a still-offline device keeps the rest of its backlog in
// order rather than hammering the pod. Serial, never overlapping. `onFlushed(id)`
// (optional) fires per success so the UI can clear that message's "not saved"
// note. Returns { flushed, remaining }.
let _flushing = false;
export async function podQueueFlush(writeFn, onFlushed) {
    if (typeof writeFn !== 'function') return { flushed: 0, remaining: 0 };
    if (_flushing) return { flushed: 0, remaining: await podQueueCount() };
    _flushing = true;
    let flushed = 0;
    try {
        const rows = await podQueueList();
        for (const row of rows) {
            let ok = false;
            try { ok = await writeFn(row); } catch (_) { ok = false; }
            if (!ok) break;    // still failing (offline / rejected): keep the ordered backlog
            await podQueueRemove(row.message_id);
            flushed++;
            try { if (onFlushed) onFlushed(row.message_id); } catch (_) { /* ignore */ }
        }
    } finally {
        _flushing = false;
    }
    return { flushed, remaining: await podQueueCount() };
}
