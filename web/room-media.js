// Account-scoped durable media drafts. Uploads are confirmed before their
// signed message is published; failures keep the original destination and blob.
import { createRoomAttachments, ROOM_ASSET_MAX } from './room-attachments.js';
import { escHtml } from './util.js';
import { t } from './i18n.js';
export function createRoomMediaStore(dbApi = globalThis.indexedDB) {
    async function operation(mode, work) {
        const db = await new Promise((resolve, reject) => {
            const request = dbApi.open('proxion-room-media-v1', 1);
            request.onupgradeneeded = () => request.result.createObjectStore('drafts', { keyPath: 'key' });
            request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
        });
        try { return await new Promise((resolve, reject) => {
            const tx = db.transaction('drafts', mode), request = work(tx.objectStore('drafts'), tx);
            let result; request.onsuccess = () => { result = request.result; };
            tx.oncomplete = () => resolve(result); tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error);
        }); } finally { db.close(); }
    }
    return { list: async actor => (await operation('readonly', store => store.getAll())).filter(row => row.actor === actor),
        put: row => operation('readwrite', (store, tx) => {
            const request = store.getAll();
            request.addEventListener('success', () => {
                if (!request.result.some(existing => existing.key === row.key)
                    && request.result.filter(existing => existing.actor === row.actor).length >= 10) tx.abort();
                else store.put(row);
            });
            return request;
        }), remove: key => operation('readwrite', store => store.delete(key)) };
}
export function createRoomMedia({ getContext, post, onMessage, showToast, store = createRoomMediaStore(), doc = document }) {
    const busy = new Set(), cancelled = new Set(), progress = new Map(); let generation = 0, displayed = null;
    const panel = () => doc.getElementById('room-media-drafts');
    async function refresh() {
        const token = ++generation, ctx = getContext(), element = panel();
        if (!element) return;
        const destination = ctx ? ctx.actor + '\n' + ctx.roomId : null;
        if (destination !== displayed) { displayed = destination; element.hidden = true; element.replaceChildren(); }
        if (!ctx) { element.hidden = true; element.replaceChildren(); return; }
        let rows;
        try { rows = (await store.list(ctx.actor)).filter(row => row.roomId === ctx.roomId && row.container === ctx.container); }
        catch { return; }
        if (token !== generation || !ctx.current()) return;
        element.hidden = !rows.length;
        element.innerHTML = rows.map(row => `<div data-room-media-draft="${escHtml(row.key)}"><span>${escHtml(row.name)} — ${escHtml(busy.has(row.key)
            ? t('roomMedia.uploading', { count: progress.get(row.key) || 0 }) : t('roomMedia.pending'))}</span> `
            + `<button type="button" class="btn btn--sm" data-room-media-retry="${escHtml(row.key)}"${busy.has(row.key) ? ' disabled' : ''}>${escHtml(t('btn.retry'))}</button> `
            + `<button type="button" class="btn btn--sm" data-room-media-cancel="${escHtml(row.key)}"${busy.has(row.key) ? ' disabled' : ''}>${escHtml(t('btn.cancel'))}</button></div>`).join('');
    }
    async function attempt(row, ctx) {
        if (busy.has(row.key) || !ctx?.current() || row.actor !== ctx.actor || row.roomId !== ctx.roomId || row.container !== ctx.container) return;
        busy.add(row.key); cancelled.delete(row.key); refresh();
        const current = () => ctx.current() && !cancelled.has(row.key);
        try {
            if (!row.content) {
                const result = await createRoomAttachments({ fetch: ctx.fetch, current }).upload(row.container, row.blob,
                    { id: row.id, name: row.name, mime: row.mime, kind: row.kind, durationMs: row.durationMs, spoiler: row.spoiler }, async chunks => {
                        if (!current()) throw Error('context changed');
                        const previous = progress.get(row.key) || 0;
                        row.chunks = chunks; progress.set(row.key, Math.round(chunks.length / Math.max(1, Math.ceil(row.blob.size / 32768)) * 100));
                        await store.put(row);
                        if (Math.floor(previous / 25) !== Math.floor(progress.get(row.key) / 25)) refresh();
                    }, row.chunks || []);
                if (!result.ok || !current()) throw Error(result.reason);
                row.content = result.content; await store.put(row);
            }
            if (!current()) return;
            const result = await post(row, ctx, current);
            if (!result.ok || !current()) throw Error('message unconfirmed');
            await store.remove(row.key);
            if (current()) onMessage(result.message, ctx);
        } catch { if (current()) showToast(t('roomMedia.failed'), 'error'); }
        finally { busy.delete(row.key); progress.delete(row.key); refresh(); }
    }
    panel()?.addEventListener('click', async event => {
        const retry = event.target.closest('[data-room-media-retry]'), cancel = event.target.closest('[data-room-media-cancel]');
        const key = retry?.dataset.roomMediaRetry || cancel?.dataset.roomMediaCancel, ctx = getContext();
        if (!key || !ctx) return;
        try {
            const row = (await store.list(ctx.actor)).find(candidate => candidate.key === key && candidate.roomId === ctx.roomId);
            if (!row || !ctx.current()) return;
            if (cancel && !busy.has(key)) {
                cancelled.add(key); await store.remove(key); cancelled.delete(key); refresh();
            } else if (retry) await attempt(row, ctx);
        } catch { if (ctx.current()) showToast(t('roomMedia.storageFailed'), 'error'); }
    });
    async function send(blob, metadata, target) {
        const ctx = getContext(target);
        if (!ctx || !ctx.current() || !blob || blob.size > ROOM_ASSET_MAX) { showToast(t('file.roomTooLarge', { mb: 2 }), 'error'); return; }
        if (metadata.kind === 'voice' && blob.size === 0) { showToast(t('voiceMsg.readFailed'), 'error'); return; }
        let rows;
        try { rows = await store.list(ctx.actor); }
        catch { showToast(t('roomMedia.storageFailed'), 'error'); return; }
        if (rows.length >= 10) { showToast(t('roomMedia.limit'), 'error'); return; }
        const id = crypto.randomUUID(), row = { key: ctx.actor + '\n' + id, id, actor: ctx.actor, roomId: ctx.roomId, container: ctx.container,
            timestamp: new Date().toISOString(), senderName: ctx.senderName || '', blob, name: metadata.name, mime: metadata.mime, kind: metadata.kind || 'file', durationMs: metadata.durationMs, spoiler: metadata.spoiler === true };
        if (!ctx.current()) return;
        try { await store.put(row); }
        catch { showToast(t('roomMedia.storageFailed'), 'error'); return; }
        await attempt(row, ctx);
    }
    return { send, refresh };
}
