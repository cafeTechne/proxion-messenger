import { FILE_MIMES, FILE_TAG_BYTES, prepareSecondaryFile, sanitizeFilename } from './e2e-secondary.js';
import { dmEnvelopeFingerprint } from './dm-receipts.js';

export const NATIVE_INLINE_BYTES = 512 * 1024;
export const NATIVE_MEDIA_DRAFT_LIMIT = 10;
const DB_NAME = 'proxion-native-media-v1';
const fail = message => Error(message);
const keyFor = (scope, id) => JSON.stringify([scope, id]);

// The source and prepared ciphertext live in IndexedDB, never localStorage.
// Admission and stage transitions are transactional: a stale scan cannot
// replace a file key or advance a different account's draft.
export function createNativeMediaStore(dbApi = globalThis.indexedDB) {
    async function operation(mode, work, current) {
        if (!current()) throw fail('Native media context changed');
        const db = await new Promise((resolve, reject) => {
            let blocked = false;
            const request = dbApi.open(DB_NAME, 1);
            request.onupgradeneeded = () => request.result.createObjectStore('drafts', { keyPath: 'key' }).createIndex('scope', 'scope');
            request.onerror = () => reject(request.error);
            request.onblocked = () => { blocked = true; reject(fail('Native media storage blocked')); };
            request.onsuccess = () => { if (blocked) { request.result.close(); return; } request.result.onversionchange = () => request.result.close(); resolve(request.result); };
        });
        try {
            if (!current()) throw fail('Native media context changed');
            return await new Promise((resolve, reject) => {
                const tx = db.transaction('drafts', mode);
                let result, error;
                const abort = message => { error = fail(message); tx.abort(); };
                tx.oncomplete = () => current() ? resolve(result) : reject(fail('Native media context changed'));
                tx.onabort = tx.onerror = () => reject(error || tx.error || fail('Native media storage failed'));
                work(tx.objectStore('drafts'), value => { result = value; }, abort);
            });
        } finally { db.close(); }
    }
    return {
        get(scope, id, current = () => true) {
            return operation('readonly', (store, done) => {
                const request = store.get(keyFor(scope, id)); request.onsuccess = () => done(request.result || null);
            }, current);
        },
        list(scope, current = () => true) {
            return operation('readonly', (store, done) => {
                const request = store.index('scope').getAll(scope); request.onsuccess = () => done(request.result);
            }, current);
        },
        insert(source, current = () => true) {
            if (typeof source.scope !== 'string' || !source.scope || source.scope.length > 8192
                || typeof source.id !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(source.id) || !(source.blob instanceof Blob)
                || source.blob.size < 1 || source.blob.size + FILE_TAG_BYTES > NATIVE_INLINE_BYTES) throw fail('Invalid native media draft');
            return operation('readwrite', (store, done, abort) => {
                const key = keyFor(source.scope, source.id), prior = store.get(key);
                prior.onsuccess = () => {
                    if (!current()) { abort('Native media context changed'); return; }
                    if (prior.result) { abort('Native media draft already exists'); return; }
                    const count = store.index('scope').count(source.scope);
                    count.onsuccess = () => {
                        if (!current()) { abort('Native media context changed'); return; }
                        if (count.result >= NATIVE_MEDIA_DRAFT_LIMIT) { abort('Native media draft limit'); return; }
                        const row = { ...source, key, stage: 'source', revision: 1 };
                        delete row.prepared; delete row.hdr;
                        store.add(row); done(row);
                    };
                };
            }, current);
        },
        transition(scope, id, revision, next, value, current = () => true) {
            return operation('readwrite', (store, done, abort) => {
                const request = store.get(keyFor(scope, id));
                request.onsuccess = () => {
                    const row = request.result;
                    if (!current()) { abort('Native media context changed'); return; }
                    if (!row || row.revision !== revision) { abort('Native media draft changed'); return; }
                    if (row.stage === 'source' && next === 'prepared' && value?.ct instanceof Uint8Array
                            && value.ct.length === row.blob.size + FILE_TAG_BYTES) row.prepared = value;
                    else if (row.stage === 'prepared' && next === 'sealed' && value?.e2e === true
                            && typeof value.content === 'string' && typeof value.nonce === 'string'
                            && typeof value.ratchet_pub === 'string' && Number.isSafeInteger(value.msg_num)
                            && value.msg_num >= 0 && JSON.stringify(value).length <= 4096) row.hdr = value;
                    else { abort('Invalid native media stage'); return; }
                    row.stage = next; row.revision++;
                    store.put(row); done(row);
                };
            }, current);
        },
    };
}

// Preparation only: gateway acceptance and source release are deliberately not
// inferred here. The native sender adapter must supply those boundaries.
export function createNativeMediaPreparation({ secondary, getScope, getGeneration = () => 0,
    isActive = () => true, store = createNativeMediaStore() }) {
    const queues = new Map();
    function context() {
        const scope = getScope(), generation = getGeneration();
        return { scope, generation, current: () => scope === getScope() && generation === getGeneration() && isActive() };
    }
    function assert(ctx) { if (!ctx.current()) throw fail('Native media context changed'); }
    async function enqueue(blob, view, metadata, id = crypto.randomUUID()) {
        const ctx = context(); assert(ctx);
        const peer = secondary.peerFor(view), mime = String(metadata.mime || '').toLowerCase().split(';')[0].trim();
        if (!peer?.startsWith('did:key:') || !['dm', 'local_dm'].includes(view.type)
            || typeof view.id !== 'string' || !view.id || view.id.length > 2048 || !FILE_MIMES.has(mime)) throw fail('Unsupported native media destination');
        return store.insert({ scope: ctx.scope, id, blob, peer,
            view: { type: view.type, id: view.id, peerWebid: peer },
            metadata: { name: sanitizeFilename(metadata.name), mime, spoiler: metadata.spoiler === true,
                ...(Number.isInteger(metadata.width) && Number.isInteger(metadata.height) ? { width: metadata.width, height: metadata.height } : {}) },
            timestamp: new Date().toISOString(),
        }, ctx.current);
    }
    function prepare(id) {
        const ctx = context(), queueKey = JSON.stringify([ctx.scope, ctx.generation, id]);
        const run = (queues.get(queueKey) || Promise.resolve()).catch(() => {}).then(async () => {
            assert(ctx);
            let row = await store.get(ctx.scope, id, ctx.current);
            if (!row) throw fail('Native media draft missing');
            if (row.scope !== ctx.scope || row.id !== id || !row.peer?.startsWith('did:key:')
                    || secondary.peerFor(row.view) !== row.peer) throw fail('Native media destination changed');
            if (row.stage === 'source') {
                const bytes = new Uint8Array(await row.blob.arrayBuffer()); assert(ctx);
                const prepared = await prepareSecondaryFile(bytes, row.metadata, { current: ctx.current });
                row = await store.transition(ctx.scope, id, row.revision, 'prepared', prepared, ctx.current);
            }
            if (row.stage === 'prepared') {
                const sendId = await dmEnvelopeFingerprint({ scope: ctx.scope, message_id: id, kind: 'native-inline-file' }); assert(ctx);
                const sealed = await secondary.sealPreparedFile(row.view, row.prepared, { sendId, current: ctx.current, echo: false });
                if (!sealed) throw fail('Native media peer key unavailable');
                row = await store.transition(ctx.scope, id, row.revision, 'sealed', sealed.hdr, ctx.current);
            }
            assert(ctx); return row;
        });
        queues.set(queueKey, run);
        run.finally(() => { if (queues.get(queueKey) === run) queues.delete(queueKey); }).catch(() => {});
        return run;
    }
    return { enqueue, prepare, list: () => { const ctx = context(); return store.list(ctx.scope, ctx.current); } };
}
