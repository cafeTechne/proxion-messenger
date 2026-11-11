// Account-scoped durable DM media drafts. Prepared ciphertext and manifests
// are persisted before network I/O; retries reuse the deterministic key and
// resource names. Deliveries are acknowledged before removing the draft.
import { createDmAttachments, prepareDmAttachment, parseDmAttachment, DM_ASSET_MAX } from './dm-attachments.js';
import { escHtml } from './util.js';
import { t } from './i18n.js';
import { sanitizeFilename } from './e2e-secondary.js';

export function createDmMediaStore(dbApi = globalThis.indexedDB) {
    async function operation(mode, work) {
        const db = await new Promise((resolve, reject) => {
            let blocked = false;
            const request = dbApi.open('proxion-dm-media-v1', 2);
            request.onupgradeneeded = event => {
                if (event.oldVersion === 0) request.result.createObjectStore('drafts', { keyPath: 'key' });
                else {
                    // Old writers did not commit a delivery boundary. Existing
                    // drafts can still retry/cancel, but cannot prove safe cleanup.
                    const cursor = request.transaction.objectStore('drafts').openCursor();
                    cursor.onsuccess = () => {
                        if (!cursor.result) return;
                        cursor.result.update({ ...cursor.result.value, deliveryState: 'unknown' });
                        cursor.result.continue();
                    };
                }
            };
            request.onblocked = () => { blocked = true; reject(Error('close older tabs before updating media drafts')); };
            request.onsuccess = () => {
                if (blocked) { request.result.close(); return; }
                request.result.onversionchange = () => request.result.close();
                resolve(request.result);
            };
            request.onerror = () => reject(request.error);
        });
        try {
            return await new Promise((resolve, reject) => {
                const tx = db.transaction('drafts', mode), request = work(tx.objectStore('drafts'), tx);
                let result;
                request.onsuccess = () => { result = request.result; };
                tx.oncomplete = () => resolve(result);
                tx.onerror = () => reject(tx.error);
                tx.onabort = () => reject(tx.error);
            });
        } finally {
            db.close();
        }
    }
    return {
        list: async actor => (await operation('readonly', store => store.getAll())).filter(row => row.actor === actor),
        put: row => operation('readwrite', (store, tx) => {
            const request = store.getAll();
            request.addEventListener('success', () => {
                if (!request.result.some(existing => existing.key === row.key)
                    && request.result.filter(existing => existing.actor === row.actor).length >= 10) tx.abort();
                else store.put(row);
            });
            return request;
        }),
        remove: key => operation('readwrite', store => store.delete(key)),
    };
}

// Each delivery attempt uses fresh ratchet messages; retrying a spent ciphertext
// under a new inbox URL would leave an undecryptable replay at the recipient.
// File encryption/prepared chunks stay unchanged across these attempts.
export async function deliverDmMedia(draft, ctx, { current, obtainKey, hasKey, encrypt, cachePeerPub,
    getPeerDevices, getOwnDevices, getDeviceId, getPub, dropDm, dropFanout }) {
    await obtainKey(ctx.peerWebId);
    if (!current()) return false;
    const peers = getPeerDevices(), own = getOwnDevices(), pub = getPub();
    const entry = async (account, device) => {
        if (!current()) throw Error('context changed');
        if (!device.device_id || !device.pub_b64u) throw Error('missing device key');
        const pid = account + '#' + device.device_id;
        cachePeerPub(pid, device.pub_b64u);
        const enc = await encrypt(pid, draft.content);
        return { to_webid: account, to_device_id: device.device_id, payload: {
            content: enc.ciphertext, e2e: true, nonce: enc.nonce, msg_num: enc.msgNum,
            pn: enc.pn, ratchet_pub: enc.ratchetPub, from_device_id: getDeviceId(),
            thread_id: ctx.threadId, thread_peer: ctx.peerWebId, ...(pub ? { x25519_pub: pub } : {}),
        } };
    };
    if (peers.length >= 2) {
        const fanout = [];
        for (const device of peers) fanout.push(await entry(ctx.peerWebId, device));
        for (const device of own) fanout.push(await entry(ctx.actor, device));
        return current() && await dropFanout({ message_id: draft.id, fanout });
    }
    if (!hasKey(ctx.peerWebId)) return false; // plaintext opt-in never exposes a file key
    const enc = await encrypt(ctx.peerWebId, draft.content);
    if (!current()) return false;
    const accepted = await dropDm({ target_webid: ctx.peerWebId, thread_id: ctx.threadId, message_id: draft.id,
        content: enc.ciphertext, e2e: true, nonce: enc.nonce, msg_num: enc.msgNum,
        pn: enc.pn, ratchet_pub: enc.ratchetPub, ...(pub ? { x25519_pub: pub } : {}) });
    if (accepted && current() && own.length) {
        try {
            const fanout = [];
            for (const device of own) fanout.push(await entry(ctx.actor, device));
            if (current()) await dropFanout({ message_id: draft.id, fanout });
        } catch { /* self-sync failure cannot undo confirmed peer delivery */ }
    }
    return !!accepted;
}

export function createDmMedia({ getContext, post, onMessage, showToast, confirmCleanup = async () => false, store = createDmMediaStore(), doc = document }) {
    const busy = new Set(), cancelled = new Set(), progress = new Map();
    let generation = 0, displayed = null;
    const panel = () => doc.getElementById('dm-media-drafts');

    async function refresh() {
        const token = ++generation, ctx = getContext(), element = panel();
        if (!element) return;
        const destination = ctx ? ctx.actor + '\n' + ctx.peerWebId : null;
        if (destination !== displayed) {
            displayed = destination;
            element.hidden = true;
            element.replaceChildren();
        }
        if (!ctx) {
            element.hidden = true;
            element.replaceChildren();
            return;
        }
        let rows;
        try {
            rows = (await store.list(ctx.actor)).filter(row => row.peerWebId === ctx.peerWebId && row.threadId === ctx.threadId);
        } catch {
            return;
        }
        if (token !== generation || !ctx.current()) return;
        element.hidden = !rows.length;
        element.innerHTML = rows.map(row => {
            const cleaning = row.cleanup === true, active = busy.has(row.key);
            const status = active ? (cleaning ? t('dmMedia.cleaning') : t('roomMedia.uploading', { count: progress.get(row.key) || 0 }))
                : (cleaning ? t('dmMedia.cleanupPending') : t('roomMedia.pending'));
            const disabled = active ? ' disabled' : '';
            return `<div data-dm-media-draft="${escHtml(row.key)}"><span>${escHtml(row.name)} — ${escHtml(status)}</span> `
                + (cleaning ? '' : `<button type="button" class="btn btn--sm" data-dm-media-retry="${escHtml(row.key)}"${disabled}>${escHtml(t('btn.retry'))}</button> `)
                + (row.deliveryState === 'never' && !row.delivered
                    ? `<button type="button" class="btn btn--sm" data-dm-media-cleanup="${escHtml(row.key)}"${disabled}>${escHtml(t(cleaning ? 'dmMedia.retryCleanup' : 'dmMedia.removeUnused'))}</button> ` : '')
                + `<button type="button" class="btn btn--sm" title="${escHtml(t('dmMedia.discardHint'))}" data-dm-media-cancel="${escHtml(row.key)}"${disabled}>${escHtml(t('btn.cancel'))}</button></div>`;
        }).join('');
    }

    function matches(row, ctx) {
        const prepared = row.cleanup ? row.cleanupPrepared : row.prepared;
        const manifest = parseDmAttachment(prepared?.content);
        return ctx && manifest && row.actor === ctx.actor && row.peerWebId === ctx.peerWebId && row.threadId === ctx.threadId
            && row.key === ctx.actor + '\n' + row.id && row.id === manifest.id
            && (row.cleanup ? row.content === '' && row.uploaded === false : row.content === prepared.content);
    }
    async function cleanup(row, ctx) {
        if (busy.has(row.key) || !ctx?.current() || !matches(row, ctx) || row.deliveryState !== 'never' || row.delivered) return;
        // Lock the draft while the confirmation is open as well as during I/O.
        busy.add(row.key); refresh();
        const current = () => ctx.current();
        try {
            if (!row.cleanup && !await confirmCleanup()) return;
            if (!current()) return;
            if (!row.cleanup) {
                row.cleanupPrepared = row.prepared;
                row.prepared = null; row.content = ''; row.uploaded = false; row.blob = null;
            }
            row.cleanup = true;
            await store.put(row); // persist intent before the first deletion
            if (!current()) return;
            const result = await createDmAttachments({ fetch: ctx.fetch, current, getSelfWebId: () => ctx.actor })
                .discardUnused(ctx.actor, row.cleanupPrepared);
            if (!result.ok || !current()) throw Error('cleanup unconfirmed');
            await store.remove(row.key);
            if (current()) showToast(t('dmMedia.cleaned'));
        } catch { if (current()) showToast(t('dmMedia.cleanupFailed'), 'error'); }
        finally { busy.delete(row.key); refresh(); }
    }

    async function attempt(row, ctx) {
        if (busy.has(row.key) || row.cleanup || !ctx?.current() || !matches(row, ctx)) return;
        busy.add(row.key);
        cancelled.delete(row.key);
        refresh();
        const current = () => ctx.current() && !cancelled.has(row.key);
        try {
            if (!row.uploaded) {
                const result = await createDmAttachments({
                    fetch: ctx.fetch, current, getSelfWebId: () => ctx.actor,
                }).upload(ctx.actor, row.prepared, async (completed, total) => {
                    if (!current()) throw Error('context changed');
                    const previous = progress.get(row.key) || 0;
                    progress.set(row.key, Math.round(completed / total * 100));
                    if (Math.floor(previous / 25) !== Math.floor((progress.get(row.key) || 0) / 25)) refresh();
                });
                if (!result.ok || !current()) throw Error(result.reason || 'upload_failed');
                row.uploaded = true;
                row.content = result.content;
                await store.put(row);
            }
            if (!current()) return;
            // Once this is committed, even a lost delivery response cannot make
            // this upload eligible for deletion. Older unmarked drafts stay unknown.
            if (row.deliveryState === 'never') {
                row.deliveryState = 'attempted';
                await store.put(row);
            }
            if (!current()) return;
            const result = await post(row, ctx, current);
            if (!result.ok) throw Error('delivery unconfirmed');
            // A delivery can finish after navigation. Keep that acknowledgement
            // in the original account's draft so retry doesn't send it again.
            row.delivered = result.message;
            await store.put(row);
            if (!current()) return;
            if (result.historySaved === false) throw Error('history unconfirmed');
            await store.remove(row.key);
            if (current() && onMessage) await onMessage(result.message, ctx);
        } catch {
            if (current()) showToast(t('file.sendFailed'), 'error');
        } finally {
            busy.delete(row.key);
            progress.delete(row.key);
            refresh();
        }
    }

    panel()?.addEventListener('click', async event => {
        const retry = event.target.closest('[data-dm-media-retry]'), cancel = event.target.closest('[data-dm-media-cancel]'), clean = event.target.closest('[data-dm-media-cleanup]');
        const key = retry?.dataset.dmMediaRetry || cancel?.dataset.dmMediaCancel || clean?.dataset.dmMediaCleanup, ctx = getContext();
        if (!key || !ctx) return;
        try {
            const row = (await store.list(ctx.actor)).find(candidate => candidate.key === key && candidate.peerWebId === ctx.peerWebId && candidate.threadId === ctx.threadId);
            if (!row || !ctx.current()) return;
            if (cancel && !busy.has(key)) {
                cancelled.add(key);
                await store.remove(key);
                cancelled.delete(key);
                refresh();
            } else if (clean) {
                await cleanup(row, ctx);
            } else if (retry) {
                await attempt(row, ctx);
            }
        } catch {
            if (ctx.current()) showToast(t('roomMedia.storageFailed'), 'error');
        }
    });

    async function send(blob, metadata, target) {
        metadata = { ...metadata, name: sanitizeFilename(metadata.name) };
        const ctx = getContext(target);
        if (!ctx || !ctx.current() || !blob || blob.size > DM_ASSET_MAX) {
            showToast(t('file.podDmTooLarge'), 'error');
            return;
        }
        if (metadata.kind === 'voice' && blob.size === 0) {
            showToast(t('voiceMsg.readFailed'), 'error');
            return;
        }
        let rows;
        try {
            rows = await store.list(ctx.actor);
        } catch {
            showToast(t('roomMedia.storageFailed'), 'error');
            return;
        }
        if (rows.length >= 10) {
            showToast(t('roomMedia.limit'), 'error');
            return;
        }
        const id = crypto.randomUUID();
        const prep = await prepareDmAttachment(blob, metadata, { id, current: ctx.current });
        if (!prep.ok || !ctx.current()) {
            if (ctx.current()) showToast(t('file.sendFailed'), 'error');
            return;
        }
        const row = {
            key: ctx.actor + '\n' + id,
            id,
            actor: ctx.actor,
            peerWebId: ctx.peerWebId,
            threadId: ctx.threadId,
            timestamp: new Date().toISOString(),
            senderName: ctx.senderName || '',
            blob,
            name: metadata.name,
            mime: metadata.mime,
            kind: metadata.kind || 'file',
            durationMs: metadata.durationMs,
            spoiler: metadata.spoiler === true,
            prepared: { ciphertext: prep.ciphertext, content: prep.content },
            content: prep.content,
            uploaded: false,
            deliveryState: 'never',
        };
        if (!ctx.current()) return;
        try {
            await store.put(row);
        } catch {
            showToast(t('roomMedia.storageFailed'), 'error');
            return;
        }
        await attempt(row, ctx);
    }

    return { send, refresh };
}
