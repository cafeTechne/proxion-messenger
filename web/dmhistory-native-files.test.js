import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { IDBFactory, IDBKeyRange, IDBObjectStore } from 'fake-indexeddb';

const account = vi.hoisted(() => ({ id: '' }));
vi.mock('./auth.js', () => ({ accountDbName: name => account.id ? name + '::' + account.id : name }));
let history;
const name = () => 'proxion-dm-history' + (account.id ? '::' + account.id : '');
const message = (id = 'native:alice:file', size = 19) => ({ message_id: id, thread_id: 'thread',
    from_webid: 'did:key:alice', content: '📎 private.txt', timestamp: '2026-11-01T00:00:00.000Z',
    file: { filename: 'private.txt', mime_type: 'text/plain', size } });
async function raw(store, key) {
    const db = await new Promise((resolve, reject) => {
        const request = indexedDB.open(name(), 2); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
    });
    try {
        return await new Promise((resolve, reject) => {
            const tx = db.transaction(store), request = key === undefined ? tx.objectStore(store).getAll() : tx.objectStore(store).get(key);
            let result; request.onsuccess = () => { result = request.result; };
            tx.oncomplete = () => resolve(result); tx.onerror = () => reject(tx.error);
        });
    } finally { db.close(); }
}
beforeEach(async () => {
    vi.resetModules(); account.id = '';
    globalThis.indexedDB = new IDBFactory(); globalThis.IDBKeyRange = IDBKeyRange;
    history = await import('./dmhistory.js');
});
afterEach(() => vi.restoreAllMocks());

describe('durable native file history', () => {
    it('atomically stores a Blob with its reference and restores exact 25 MiB bytes after reopening', async () => {
        const bytes = new Uint8Array(25 * 1024 * 1024).fill(65), blob = new Blob([bytes]), msg = message(undefined, bytes.length);
        expect(await history.dmHistorySaveFile(msg, blob)).toBe(true);
        const saved = await raw('messages', msg.message_id), files = await raw('native_files');
        expect(saved.file).toMatchObject({ _nativeAsset: msg.message_id, size: bytes.length });
        expect(saved.file.data_b64).toBeUndefined(); expect(files).toHaveLength(1); expect(files[0].blob.size).toBe(bytes.length);
        vi.resetModules(); history = await import('./dmhistory.js');
        const loaded = await history.dmHistoryGet(msg.message_id);
        const restored = Uint8Array.from(atob(loaded.file.data_b64), char => char.charCodeAt(0));
        expect(restored.length).toBe(bytes.length);
        expect(new Uint8Array(await crypto.subtle.digest('SHA-256', restored))).toEqual(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)));
        const preview = (await history.dmHistorySearch('private.txt'))[0];
        expect(preview.file.data_b64).toBeUndefined(); expect(preview.file._nativeAsset).toBeUndefined();
        expect((await history.dmHistoryExportRecent())[0].file._nativeAsset).toBeUndefined();
    }, 20000);
    it.each(['native_files', 'messages'])('does not leave either half behind when %s persistence fails', async store => {
        const put = IDBObjectStore.prototype.put;
        vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (...args) {
            if (this.name === store) throw new DOMException('storage full', 'QuotaExceededError');
            return put.apply(this, args);
        });
        expect(await history.dmHistorySaveFile(message(), new Blob(['private file bytes!']))).toBe(false);
        expect(await raw('native_files')).toEqual([]); expect(await raw('messages')).toEqual([]);
    });
    it('requires enabled history, the original context and an exact blob size', async () => {
        const blob = new Blob(['private file bytes!']);
        history.dmHistorySetEnabled(false); expect(await history.dmHistorySaveFile(message(), blob)).toBe(false);
        history.dmHistorySetEnabled(true); expect(await history.dmHistorySaveFile(message(), blob, { current: () => false })).toBe(false);
        expect(await history.dmHistorySaveFile(message(undefined, 1), blob)).toBe(false);
        expect(await history.dmHistoryLoad('thread')).toEqual([]);
    });
    it('keeps original bytes and later edits on an exact finalization retry and rejects conflicting ownership or bytes', async () => {
        const msg = message(), blob = new Blob(['private file bytes!']);
        expect(await history.dmHistorySaveFile(msg, blob)).toBe(true);
        await history.dmHistoryUpdateContent(msg.message_id, 'edited caption');
        expect(await history.dmHistorySaveFile(msg, blob)).toBe(true);
        expect((await history.dmHistoryGet(msg.message_id)).content).toBe('edited caption');
        expect(await history.dmHistorySaveFile({ ...msg, from_webid: 'did:key:other' }, blob)).toBe(false);
        expect(await history.dmHistorySaveFile(msg, new Blob(['changed file bytes!']))).toBe(false);
        expect(atob((await history.dmHistoryGet(msg.message_id)).file.data_b64)).toBe('private file bytes!');
        // Pairing/import or ordinary caching cannot replace this device's known
        // bytes with an unavailable metadata-only copy of the same message.
        await history.dmHistorySave({ ...msg, file: { ...msg.file, unavailable: true } });
        expect(atob((await history.dmHistoryGet(msg.message_id)).file.data_b64)).toBe('private file bytes!');
    });
    it('does not resolve a foreign message reference to another message’s private bytes', async () => {
        const msg = message(), blob = new Blob(['private file bytes!']); await history.dmHistorySaveFile(msg, blob);
        const foreign = message('native:foreign:other'); foreign.from_webid = 'did:key:foreign'; foreign.file._nativeAsset = msg.message_id;
        await history.dmHistorySave(foreign);
        const loaded = await history.dmHistoryGet(foreign.message_id);
        expect(loaded.file.unavailable).toBe(true); expect(loaded.file.data_b64).toBeUndefined();
        await history.dmHistoryDelete(foreign.message_id);
        expect(atob((await history.dmHistoryGet(msg.message_id)).file.data_b64)).toBe('private file bytes!');
    });
    it('does not return old-account bytes after the account changes during Blob loading', async () => {
        const blob = new Blob(['private file bytes!']), msg = message(); await history.dmHistorySaveFile(msg, blob);
        const buffer = await blob.arrayBuffer(); let release;
        vi.spyOn(Blob.prototype, 'arrayBuffer').mockImplementation(() => new Promise(resolve => { release = resolve; }));
        const loading = history.dmHistoryGet(msg.message_id);
        await vi.waitFor(() => expect(release).toBeTypeOf('function'));
        account.id = 'other'; release(buffer); expect(await loading).toBeNull();
    });
    it('does not commit an old-account file if the account changes before its bytes finish reading', async () => {
        const blob = new Blob(['private file bytes!']), buffer = await blob.arrayBuffer(); let release;
        vi.spyOn(blob, 'arrayBuffer').mockImplementation(() => new Promise(resolve => { release = resolve; }));
        const saving = history.dmHistorySaveFile(message(), blob);
        await vi.waitFor(() => expect(release).toBeTypeOf('function'));
        account.id = 'other'; release(buffer); expect(await saving).toBe(false);
        expect(await history.dmHistoryLoad('thread')).toEqual([]);
        account.id = ''; expect(await history.dmHistoryLoad('thread')).toEqual([]);
    });
    it('keeps a corrupted file explicitly unavailable without hiding other history', async () => {
        const msg = message(); await history.dmHistorySaveFile(msg, new Blob(['private file bytes!']));
        await history.dmHistorySave({ message_id: 'text', thread_id: 'thread', content: 'other readable message' });
        const asset = await raw('native_files', msg.message_id);
        const db = await new Promise(resolve => { const req = indexedDB.open(name(), 2); req.onsuccess = () => resolve(req.result); });
        await new Promise(resolve => {
            const tx = db.transaction('native_files', 'readwrite');
            tx.objectStore('native_files').put({ ...asset, blob: new Blob([new Uint8Array(asset.size).fill(66)]) });
            tx.oncomplete = resolve;
        }); db.close();
        const rows = await history.dmHistoryLoad('thread');
        expect(rows).toHaveLength(2); expect(rows.find(row => row.message_id === 'text').content).toBe('other readable message');
        const file = rows.find(row => row.message_id === msg.message_id).file;
        expect(file.unavailable).toBe(true); expect(file.data_b64).toBeUndefined();
    });
    it('preserves version-one history and fails promptly while an older tab blocks the upgrade', async () => {
        const db = await new Promise(resolve => {
            const request = indexedDB.open(name(), 1);
            request.onupgradeneeded = () => request.result.createObjectStore('messages', { keyPath: 'message_id' }).createIndex('thread_id', 'thread_id');
            request.onsuccess = () => resolve(request.result);
        });
        await new Promise(resolve => {
            const tx = db.transaction('messages', 'readwrite'); tx.objectStore('messages').put({ message_id: 'old', thread_id: 'thread', content: 'original history' });
            tx.oncomplete = resolve;
        });
        expect(await history.dmHistoryLoad('thread')).toEqual([]); // upgrade is blocked, not silently replaced
        db.close();
        const rows = await history.dmHistoryLoad('thread'); expect(rows).toHaveLength(1); expect(rows[0].content).toBe('original history');
        expect(await history.dmHistorySaveFile(message(), new Blob(['private file bytes!']))).toBe(true);
    });
    it('does not resurrect a deleted message when its Blob load completes late', async () => {
        const blob = new Blob(['private file bytes!']), msg = message(); await history.dmHistorySaveFile(msg, blob);
        const buffer = await blob.arrayBuffer(); let release;
        vi.spyOn(Blob.prototype, 'arrayBuffer').mockImplementation(() => new Promise(resolve => { release = resolve; }));
        const loading = history.dmHistoryGet(msg.message_id);
        await vi.waitFor(() => expect(release).toBeTypeOf('function'));
        await history.dmHistoryDelete(msg.message_id); release(buffer);
        expect(await loading).toBeNull(); expect(await raw('native_files')).toEqual([]);
    });
    it.each(['message', 'thread', 'expiry', 'all'])('removes Blob bytes atomically with %s history deletion', async kind => {
        const msg = message(); await history.dmHistorySaveFile(msg, new Blob(['private file bytes!']));
        if (kind === 'message') await history.dmHistoryDelete(msg.message_id);
        if (kind === 'thread') await history.dmHistoryDeleteThread(msg.thread_id);
        if (kind === 'expiry') await history.dmHistoryDeleteBefore(msg.thread_id, '2026-12-01T00:00:00.000Z');
        if (kind === 'all') await history.dmHistoryClearAll();
        expect(await raw('native_files')).toEqual([]); expect(await raw('messages')).toEqual([]);
    });
    it('bounds concurrent file count without evicting an accepted file', async () => {
        const blob = new Blob(['x']);
        for (let index = 0; index < 63; index++) expect(await history.dmHistorySaveFile(message('file-' + index, 1), blob)).toBe(true);
        const admitted = await Promise.all([history.dmHistorySaveFile(message('race-1', 1), blob), history.dmHistorySaveFile(message('race-2', 1), blob)]);
        expect(admitted.filter(Boolean)).toHaveLength(1);
        expect((await raw('native_files')).length).toBe(history.MAX_NATIVE_HISTORY_FILES);
        expect((await raw('messages')).length).toBe(history.MAX_NATIVE_HISTORY_FILES);
        expect(atob((await history.dmHistoryGet('file-0')).file.data_b64)).toBe('x');
    });
    it('reserves the complete Blob byte budget and makes capacity available after explicit deletion', async () => {
        const blob = new Blob([new Uint8Array(25 * 1024 * 1024)]);
        for (let index = 0; index < 5; index++) expect(await history.dmHistorySaveFile(message('large-' + index, blob.size), blob)).toBe(true);
        expect(await history.dmHistorySaveFile(message('large-5', blob.size), blob)).toBe(false);
        expect((await raw('native_files')).length).toBe(5);
        await history.dmHistoryDelete('large-0');
        expect(await history.dmHistorySaveFile(message('large-5', blob.size), blob)).toBe(true);
    }, 20000);
});
