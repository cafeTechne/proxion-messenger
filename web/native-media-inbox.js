import { dmEnvelopeFingerprint } from './dm-receipts.js';

import { NATIVE_CHUNK_BYTES, NATIVE_FILE_BYTES, NATIVE_HEADER_KEYS, nativeChunkManifest } from './native-media-protocol.js';
export { NATIVE_CHUNK_BYTES, NATIVE_FILE_BYTES, NATIVE_HEADER_KEYS, nativeChunkManifest } from './native-media-protocol.js';
const MAX_INCOMING = 8, MAX_BYTES = 32 * 1024 * 1024;
const digest = bytes => crypto.subtle.digest('SHA-256', bytes).then(result => [...new Uint8Array(result)].map(byte => byte.toString(16).padStart(2, '0')).join(''));
const keyFor = (scope, sender, id) => JSON.stringify([scope, sender, id]);

// Ciphertext only. The file key stays in the encrypted ratchet receive journal.
export function createNativeMediaInbox(dbApi = globalThis.indexedDB) {
    async function operation(scope, mode, current, work) {
        if (typeof scope !== 'string' || !scope || scope.length > 8192 || !current()) throw Error('Native receive context changed');
        const db = await new Promise((resolve, reject) => {
            let blocked = false;
            const request = dbApi.open('proxion-native-inbox-v1', 1);
            request.onupgradeneeded = () => {
                request.result.createObjectStore('files', { keyPath: 'key' }).createIndex('scope', 'scope');
                request.result.createObjectStore('parts', { keyPath: ['transfer', 'seq'] }).createIndex('transfer', 'transfer');
            };
            request.onerror = () => reject(request.error);
            request.onblocked = () => { blocked = true; reject(Error('Native receive storage blocked')); };
            request.onsuccess = () => {
                if (blocked) { request.result.close(); return; }
                request.result.onversionchange = () => request.result.close(); resolve(request.result);
            };
        });
        try {
            if (!current()) throw Error('Native receive context changed');
            return await new Promise((resolve, reject) => {
                const tx = db.transaction(['files', 'parts'], mode);
                let result, error;
                const fail = message => { error = Error(message); tx.abort(); };
                const safe = callback => () => {
                    try { if (!current()) fail('Native receive context changed'); else callback(); }
                    catch (err) { error = err; tx.abort(); }
                };
                tx.oncomplete = () => current() ? resolve(result) : reject(Error('Native receive context changed'));
                tx.onabort = tx.onerror = () => reject(error || tx.error || Error('Native receive storage failed'));
                work(tx.objectStore('files'), tx.objectStore('parts'), value => { result = value; }, fail, safe);
            });
        } finally { db.close(); }
    }
    return {
        async begin(scope, event, current = () => true) {
            const manifest = nativeChunkManifest(event), token = event.native_commit;
            if (event.type !== 'native_media_offer' || event.native_chunks_v !== 1
                || typeof event.from_webid !== 'string' || !event.from_webid.startsWith('did:key:') || event.from_webid.length > 2048
                || !/^[A-Za-z0-9_-]{1,128}$/.test(event.message_id || '') || token?.v !== 1
                || !Number.isSafeInteger(token.queue_id) || token.queue_id < 1 || !/^[a-f0-9]{64}$/.test(token.envelope_digest || '')) throw Error('Invalid native offer');
            const manifestDigest = await dmEnvelopeFingerprint(manifest);
            if (!current() || event.manifest_digest !== manifestDigest) throw Error('Native manifest mismatch');
            return operation(scope, 'readwrite', current, (files, parts, done, fail, safe) => {
                const key = keyFor(scope, event.from_webid, event.message_id), get = files.get(key);
                get.onsuccess = safe(() => {
                    if (get.result) {
                        if (get.result.manifestDigest !== manifestDigest) fail('Conflicting native offer');
                        else {
                            const row = get.result;
                            if (row.token.queue_id !== token.queue_id || row.token.envelope_digest !== token.envelope_digest) {
                                row.token = { v: 1, queue_id: token.queue_id, envelope_digest: token.envelope_digest }; row.revision++; files.put(row);
                            }
                            done(row);
                        }
                        return;
                    }
                    const scan = files.index('scope').getAll(scope);
                    scan.onsuccess = safe(() => {
                        if (scan.result.length >= MAX_INCOMING || scan.result.reduce((sum, row) => sum + row.manifest.size, manifest.size) > MAX_BYTES) {
                            fail('Native receive storage full'); return;
                        }
                        const row = { key, scope, sender: event.from_webid, id: event.message_id,
                            manifest, manifestDigest, token: { v: 1, queue_id: token.queue_id, envelope_digest: token.envelope_digest },
                            received: 0, bytes: 0, revision: 1,
                            ...(typeof event.timestamp === 'string' && event.timestamp.length <= 64 ? { timestamp: event.timestamp } : {}) };
                        if (typeof event.thread_id === 'string' && event.thread_id.length <= 2048) row.threadHint = event.thread_id;
                        files.add(row); done(row);
                    });
                });
            });
        },
        list(scope, current = () => true) {
            return operation(scope, 'readonly', current, (files, parts, done, fail, safe) => {
                const request = files.index('scope').getAll(scope); request.onsuccess = safe(() => done(request.result));
            });
        },
        async put(scope, sender, id, manifestDigest, seq, raw, partDigest, current = () => true) {
            if (!(raw instanceof Uint8Array) || raw.length < 1 || raw.length > NATIVE_CHUNK_BYTES || !Number.isSafeInteger(seq) || seq < 0) throw Error('Invalid native part');
            const data = raw.slice(), hash = await digest(data);
            if (!current() || hash !== partDigest) throw Error('Native part digest mismatch');
            return operation(scope, 'readwrite', current, (files, parts, done, fail, safe) => {
                const key = keyFor(scope, sender, id), get = files.get(key);
                get.onsuccess = safe(() => {
                    const row = get.result;
                    if (!row || row.manifestDigest !== manifestDigest || seq >= row.manifest.chunk_count
                        || data.length !== Math.min(NATIVE_CHUNK_BYTES, row.manifest.size - seq * NATIVE_CHUNK_BYTES)) { fail('Native part does not match offer'); return; }
                    const prior = parts.get([key, seq]);
                    prior.onsuccess = safe(() => {
                        if (prior.result) {
                            if (prior.result.digest !== hash || prior.result.data.length !== data.length
                                || prior.result.data.some((byte, index) => byte !== data[index])) fail('Conflicting native part');
                            else done(row);
                            return;
                        }
                        parts.add({ transfer: key, seq, digest: hash, data });
                        row.received++; row.bytes += data.length; row.revision++;
                        files.put(row); done(row);
                    });
                });
            });
        },
        missing(scope, sender, id, manifestDigest, current = () => true) {
            return operation(scope, 'readonly', current, (files, parts, done, fail, safe) => {
                const key = keyFor(scope, sender, id), get = files.get(key);
                get.onsuccess = safe(() => {
                    const row = get.result;
                    if (!row || row.manifestDigest !== manifestDigest) { fail('Native offer missing'); return; }
                    const scan = parts.index('transfer').getAllKeys(key);
                    scan.onsuccess = safe(() => {
                        const known = new Set(scan.result.map(key => key[1]));
                        done(Array.from({ length: row.manifest.chunk_count }, (_, seq) => seq).filter(seq => !known.has(seq)).slice(0, 8));
                    });
                });
            });
        },
        async assemble(scope, sender, id, manifestDigest, current = () => true) {
            const result = await operation(scope, 'readonly', current, (files, parts, done, fail, safe) => {
                const key = keyFor(scope, sender, id), get = files.get(key);
                get.onsuccess = safe(() => {
                    const row = get.result;
                    if (!row || row.manifestDigest !== manifestDigest || row.received !== row.manifest.chunk_count) { fail('Native file incomplete'); return; }
                    const bytes = new Uint8Array(row.manifest.size), cursor = parts.index('transfer').openCursor(key);
                    let seq = 0;
                    cursor.onsuccess = safe(() => {
                        const part = cursor.result;
                        if (!part) { if (seq !== row.manifest.chunk_count) fail('Native file incomplete'); else done({ bytes, row }); return; }
                        const data = part.value.data;
                        if (part.value.seq !== seq || data.length !== Math.min(NATIVE_CHUNK_BYTES, bytes.length - seq * NATIVE_CHUNK_BYTES)) { fail('Native file part mismatch'); return; }
                        bytes.set(data, seq * NATIVE_CHUNK_BYTES); seq++; part.continue();
                    });
                });
            });
            if (await digest(result.bytes) !== result.row.manifest.cipher_digest || !current()) throw Error('Native ciphertext digest mismatch');
            return result;
        },
        async release(scope, sender, id, manifestDigest, receiveId, current = () => true) {
            const snapshot = await operation(scope, 'readonly', current, (files, parts, done, fail, safe) => {
                const get = files.get(keyFor(scope, sender, id)); get.onsuccess = safe(() => done(get.result));
            });
            if (!snapshot) return true;
            const header = Object.fromEntries(NATIVE_HEADER_KEYS.map(key => [key, snapshot.manifest[key]]));
            const expected = await dmEnvelopeFingerprint({ transport: 'native-media', from_webid: sender,
                message_id: id, header, ciphertext_digest: snapshot.manifest.cipher_digest });
            if (!current() || receiveId !== expected) throw Error('Native full receipt mismatch');
            return operation(scope, 'readwrite', current, (files, parts, done, fail, safe) => {
                const key = keyFor(scope, sender, id), get = files.get(key);
                get.onsuccess = safe(() => {
                    const row = get.result;
                    if (!row) { done(true); return; }
                    if (row.manifestDigest !== manifestDigest) { fail('Native file changed'); return; }
                    const scan = parts.index('transfer').getAllKeys(key);
                    scan.onsuccess = safe(() => { for (const partKey of scan.result) parts.delete(partKey); files.delete(key); done(true); });
                });
            });
        },
    };
}
