// Durable, content-free evidence that this account/device consumed an exact DM
// envelope. Never deduplicate by a sender-controlled message id or URL alone.
export const MAX_DM_RECEIPTS = 4096;
export async function dmEnvelopeFingerprint(envelope) {
    const canonical = value => {
        if (value === null || typeof value !== 'object') return JSON.stringify(value);
        if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
        return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
    };
    const bytes = new TextEncoder().encode(canonical(envelope));
    if (bytes.length > 131072) throw Error('oversized DM receipt');
    return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(value => value.toString(16).padStart(2, '0')).join('');
}

export function createDmReceiptStore(dbApi = globalThis.indexedDB, { max = MAX_DM_RECEIPTS } = {}) {
    if (!Number.isInteger(max) || max < 1 || max > MAX_DM_RECEIPTS) throw Error('invalid receipt cap');
    async function operation(scope, mode, work) {
        const db = await new Promise((resolve, reject) => {
            if (!dbApi || typeof scope !== 'string' || !scope || scope.length > 8192) { reject(Error('receipt storage unavailable')); return; }
            const request = dbApi.open('proxion-dm-receipts-v1', 1);
            request.onupgradeneeded = () => {
                const store = request.result.createObjectStore('receipts', { keyPath: 'key' });
                store.createIndex('scope', 'scope');
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
        try {
            return await new Promise((resolve, reject) => {
                const tx = db.transaction('receipts', mode), store = tx.objectStore('receipts');
                let result;
                work(store, value => { result = value; });
                tx.oncomplete = () => resolve(result);
                tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error);
            });
        } finally { db.close(); }
    }
    const key = (scope, fingerprint) => {
        if (typeof fingerprint !== 'string' || !/^[a-f0-9]{64}$/.test(fingerprint)) throw Error('receipt must be a SHA-256 digest');
        return JSON.stringify([scope, fingerprint]);
    };
    return {
        has: (scope, fingerprint) => operation(scope, 'readonly', (store, done) => {
            const request = store.get(key(scope, fingerprint)); request.onsuccess = () => done(!!request.result);
        }),
        hasSpace: scope => operation(scope, 'readonly', (store, done) => {
            const request = store.index('scope').getAll(scope);
            request.onsuccess = () => done(request.result.filter(row => !row.acknowledged).length < max);
        }),
        remember: (scope, fingerprint) => operation(scope, 'readwrite', (store, done) => {
            const rowKey = key(scope, fingerprint);
            const request = store.index('scope').getAll(scope);
            request.onsuccess = () => {
                const rows = request.result;
                if (!rows.some(row => row.key === rowKey) && rows.length >= max) {
                    const oldest = rows.filter(row => row.acknowledged).sort((a, b) => a.at - b.at)[0];
                    if (!oldest) { done(false); return; }
                    store.delete(oldest.key);
                }
                store.put({ key: rowKey, scope, fingerprint, acknowledged: false, at: Date.now() }); done(true);
            };
        }),
        acknowledge: (scope, fingerprint) => operation(scope, 'readwrite', (store, done) => {
            const request = store.get(key(scope, fingerprint));
            request.onsuccess = () => {
                if (request.result) store.put({ ...request.result, acknowledged: true, at: Date.now() });
                done(true);
            };
        }),
    };
}
