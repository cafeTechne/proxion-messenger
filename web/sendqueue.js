// sendqueue.js: the durable half of the offline send queue (connection.js).
//
// A message, edit, delete, pin or reaction made while the gateway is out of
// reach waits in connection.js's in-memory queue. This keeps a copy in
// IndexedDB so closing or reloading the tab before the connection comes back
// does not lose it: the next page load replays it after register. Rows carry
// the identity that queued them and are only replayed by that identity; the
// database is also namespaced per signed-in account (accountDbName) and
// cleared on logout. Best-effort: without IndexedDB the queue is memory-only,
// as before.

import { accountDbName } from './auth.js';

const DB_NAME = 'proxion-send-queue';
export const SEND_QUEUE_STORE = 'frames';
export const SEND_QUEUE_DB = DB_NAME;

let _dbPromise = null;
let _openName = null;
function _open() {
    const name = accountDbName(DB_NAME);
    if (_dbPromise && _openName === name) return _dbPromise;
    _openName = name;
    _dbPromise = new Promise((resolve, reject) => {
        if (typeof indexedDB === 'undefined') { reject(new Error('no-indexeddb')); return; }
        const req = indexedDB.open(name, 1);
        req.onupgradeneeded = () => {
            const db = req.result;
            if (!db.objectStoreNames.contains(SEND_QUEUE_STORE)) db.createObjectStore(SEND_QUEUE_STORE, { keyPath: 'key' });
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    }).catch((err) => { _dbPromise = null; throw err; });
    return _dbPromise;
}

function _run(mode, fn) {
    return _open().then((db) => new Promise((resolve, reject) => {
        const tx = db.transaction(SEND_QUEUE_STORE, mode);
        const out = fn(tx.objectStore(SEND_QUEUE_STORE));
        tx.oncomplete = () => resolve(out && 'result' in out ? out.result : undefined);
        tx.onerror = () => reject(tx.error);
    }));
}

// { key, owner, payload, queued_at }
export function sendQueueSave(entry) {
    if (!entry || !entry.key) return Promise.resolve();
    return _run('readwrite', (s) => { s.put(entry); });
}

export function sendQueueRemove(key) {
    if (!key) return Promise.resolve();
    return _run('readwrite', (s) => { s.delete(key); });
}

export function sendQueueLoad() {
    return _run('readonly', (s) => s.getAll()).then((rows) => rows || []);
}

export const sendQueueStore = { save: sendQueueSave, remove: sendQueueRemove, load: sendQueueLoad };
