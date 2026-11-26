import { escHtml, formatTimestamp } from './util.js';
import { t } from './i18n.js';

// A device-local review of saved sends. No message body or crypto details enter
// this view, and actions always revalidate their account/session context.
export function createDmOutboxUi({ outbox, getScope, isActive, getLegacy = () => [], reviewLegacy, reviewUnknown = () => {}, reviewMedia = () => {},
    getName = id => id, getAccountScope = getScope, subscribe = () => () => {}, isVisible = () => true, observeVisibility = () => () => {}, reviewHistory = () => {}, doc = document }) {
    const el = suffix => doc.getElementById('dm-outbox-' + suffix);
    let generation = 0, displayedScope = null, displayedAccount = null, unsubscribe = null, unwatchVisibility = null, timer = null;
    let rowsById = new Map();
    const updates = new Map();
    function stopLive() {
        unsubscribe?.(); unsubscribe = null;
        unwatchVisibility?.(); unwatchVisibility = null;
        if (timer !== null) clearTimeout(timer);
        timer = null; updates.clear();
    }
    function disconnect() {
        generation++; stopLive();
        rowsById = new Map([...rowsById].map(([id, row]) => [id, id.startsWith('native:') || id.startsWith('native-in:')
            ? { ...row, state: 'nativeConnection', canRetry: false } : row]));
        renderRows([...rowsById.values()]);
    }
    function watch(scope) {
        if (unsubscribe || !isVisible()) return;
        unwatchVisibility = observeVisibility(() => { if (!isVisible()) reset(); });
        unsubscribe = subscribe(event => {
            if (scope !== getScope() || !isActive() || !isVisible()) { reset(); return; }
            if (event.reset) { disconnect(); return; }
            if (typeof event.id !== 'string' || event.id.length > 16384) return;
            if (!updates.has(event.id) && updates.size >= 50) return;
            updates.set(event.id, event);
            if (timer !== null) return;
            timer = setTimeout(() => {
                timer = null;
                if (scope !== getScope() || !isActive() || !isVisible()) { reset(); return; }
                let inspect = false;
                for (const [id, patch] of updates) {
                    const row = rowsById.get(id);
                    if (patch.removed) rowsById.delete(id);
                    else if (row) rowsById.set(id, { ...row, state: patch.state || row.state, progress: patch.progress || row.progress, ...(patch.canCancel === false ? { canCancel: false } : {}), ...(typeof patch.canRetry === 'boolean' ? { canRetry: patch.canRetry } : {}) });
                    else inspect = true;
                }
                updates.clear();
                if (inspect) refresh(); else renderRows([...rowsById.values()]);
            }, 300);
        });
    }
    // Match rows and controls by their stable identity. Progress/status changes
    // update text and attributes without detaching the focused Retry/Decline.
    function renderRows(rows) {
        const list = el('rows');
        const html = rows.map(rowHtml).join('');
        if (!doc.createElement || !list.querySelectorAll) { list.innerHTML = html; return; }
        const scratch = doc.createElement('ul'); scratch.innerHTML = html;
        const existing = new Map([...list.children].map(row => [row.dataset.outboxRow, row]));
        for (const fresh of [...scratch.children]) {
            const old = existing.get(fresh.dataset.outboxRow);
            if (!old) { list.appendChild(fresh); continue; }
            existing.delete(fresh.dataset.outboxRow);
            const parts = new Map([...old.children].map(child => [child.dataset.outboxPart, child]));
            for (const part of [...fresh.children]) {
                const kept = parts.get(part.dataset.outboxPart);
                if (!kept || kept.tagName !== part.tagName) { old.appendChild(part); continue; }
                parts.delete(part.dataset.outboxPart);
                for (const attr of [...kept.attributes]) if (!part.hasAttribute(attr.name)) kept.removeAttribute(attr.name);
                for (const attr of [...part.attributes]) if (kept.getAttribute(attr.name) !== attr.value) kept.setAttribute(attr.name, attr.value);
                if (kept.textContent !== part.textContent) kept.textContent = part.textContent;
            }
            for (const child of parts.values()) { if (child.contains(doc.activeElement)) el('refresh')?.focus(); child.remove(); }
        }
        for (const row of existing.values()) {
            if (row.contains(doc.activeElement)) el('refresh')?.focus();
            row.remove();
        }
        const status = rows.length ? t('outbox.uncertain') : t('outbox.empty');
        if (el('status').textContent !== status) el('status').textContent = status;
    }
    const states = () => ({ nativeRate: t('nativeMedia.rateLimited'), nativeRefused: t('nativeMedia.refused'), nativeDeclined: t('nativeMedia.declined'), nativeUpload: t('nativeMedia.uploading'), nativeStorage: t('nativeMedia.storageUnavailable'), nativeIncoming: t('nativeMedia.receivingPending'), nativeConnection: t('nativeMedia.connectionRequired'), nativeBlocked: t('nativeMedia.blocked'), nativeWaiting: t('nativeMedia.waiting'), nativePeer: t('nativeMedia.peerUnavailable'),
        nativeHeader: t('nativeMedia.headerWaiting'), nativeFile: t('nativeMedia.fileWaiting'),
        nativeHistory: t('nativeMedia.historyRequired'), nativeKey: t('nativeMedia.keyChanged'), waiting: t('outbox.waiting'), confirmation: t('outbox.confirmation'),
        cleanup: t('outbox.cleanup'), account: t('outbox.originalAccount'), accountConfirmed: t('outbox.confirmedAccount'), unbound: t('outbox.unknownAccount'), media: t('dmMedia.finishHistory'), mediaAttention: t('dmMedia.originalAccount'), sync: t('outbox.sync'), syncAttention: t('outbox.syncAttention'), attention: t('outbox.attention'), storage: t('outbox.storage'), legacy: t('outbox.legacy') });
    function transferProgress(row) {
        const { received, total } = row.progress || {};
        if (!Number.isSafeInteger(received) || !Number.isSafeInteger(total) || received < 0 || total < 1 || total > 400 || received > total) return '';
        const percent = Math.floor(100 * received / total);
        return `<progress data-outbox-part="progress" value="${received}" max="${total}" aria-label="${escHtml(t('nativeMedia.progressLabel'))}"></progress>`
            + `<p data-outbox-part="percent" role="status">${escHtml(t('nativeMedia.progress', { percent }))}</p>`;
    }
    function reset() { generation++; stopLive(); rowsById.clear(); displayedScope = null; displayedAccount = null; el('rows')?.replaceChildren(); if (el('status')) el('status').textContent = ''; }
    async function refresh() {
        if (displayedAccount !== null && displayedAccount !== getAccountScope()) reset();
        else if (displayedScope !== null && displayedScope !== getScope()) stopLive();
        generation++;
        const token = generation, scope = getScope(), account = getAccountScope();
        const current = () => token === generation && scope === getScope() && account === getAccountScope() && isActive() && isVisible();
        if (!current()) { el('status').textContent = t('outbox.unavailable'); return; }
        watch(scope);
        if (!rowsById.size) el('status').textContent = t('outbox.loading');
        try {
            const pending = await outbox.inspect();
            const legacy = getLegacy().map(row => ({ id: row.message_id, peer: row.thread_id, timestamp: row.timestamp, state: 'legacy', canCancel: false }));
            if (!current()) return;
            displayedScope = scope; displayedAccount = account;
            const rows = [...pending, ...legacy];
            rowsById = new Map(rows.map(row => [row.id, row]));
            renderRows(rows);
        } catch { if (current()) el('status').textContent = t('outbox.unavailable'); }
    }
    function rowHtml(row) { return `<li data-outbox-row="${escHtml(row.id)}">` + (row.peer ? `<p data-outbox-part="peer">${escHtml(getName(row.peer))} · ${escHtml(formatTimestamp(row.timestamp))}</p>` : '')
                + `<p data-outbox-part="state">${escHtml(states()[row.state] || states().waiting)}</p>` + transferProgress(row)
                + (row.state === 'cleanup' ? '' : `<button type="button" class="btn btn--sm" data-outbox-id="${escHtml(row.id)}" data-outbox-part="retry" data-outbox-action="${row.state === 'legacy' ? 'review' : row.state === 'unbound' ? 'unknown' : row.state === 'media' ? 'media' : 'retry'}"${((row.state === 'nativeBlocked' || row.state === 'attention' || row.state === 'syncAttention' || row.state === 'mediaAttention' || row.state === 'account' || row.state === 'accountConfirmed') && !row.canRetry) ? ' disabled' : ''}${row.state === 'nativeConnection' ? ' aria-disabled="true"' : ''}>${escHtml((row.state === 'legacy' || row.state === 'unbound') ? t('outbox.review') : row.state === 'media' ? t('dmMedia.openConversation') : t('send.retry'))}</button>`)
                + (row.canDecline ? ` <button type="button" class="btn btn--sm" data-outbox-id="${escHtml(row.id)}" data-outbox-part="decline" data-outbox-action="decline"${row.state === 'nativeConnection' ? ' aria-disabled="true"' : ''}>${escHtml(t('nativeMedia.decline'))}</button>` : '')
                + (row.canCancel ? ` <button type="button" class="btn btn--sm" data-outbox-id="${escHtml(row.id)}" data-outbox-part="cancel" data-outbox-action="cancel">${escHtml(t('outbox.cancel'))}</button>` : '') + (['nativeHistory', 'nativeStorage'].includes(row.state) ? ` <button type="button" class="btn btn--sm" data-outbox-part="history" data-outbox-id="${escHtml(row.id)}" data-outbox-action="history">${escHtml(t('nativeMedia.historyPreferences'))}</button>` : '')
                + '</li>'; }
    el('refresh')?.addEventListener('click', refresh);
    el('rows')?.addEventListener('click', async event => {
        const button = event.target.closest('[data-outbox-action]');
        if (!button || button.disabled || button.getAttribute?.('aria-disabled') === 'true' || !isActive()) return;
        if (displayedScope !== getScope()) { reset(); return; }
        const scope = getScope(), id = button.dataset.outboxId, action = button.dataset.outboxAction;
        button.disabled = true;
        let cannotCancel = false, cannotDecline = false;
        try {
            if (action === 'history') reviewHistory(id);
            else if (action === 'review') await reviewLegacy(id);
            else if (action === 'unknown') await reviewUnknown(id);
            else if (action === 'media') await reviewMedia(id);
            else if (action === 'decline') cannotDecline = !await outbox.decline(id);
            else if (action === 'cancel') {
                cannotCancel = !await outbox.cancel(id);
            } else await outbox.send(id);
        } catch { if (getScope() === scope) el('status').textContent = t('outbox.unavailable'); }
        if (getScope() === scope && isActive() && action !== 'cancel') {
            await refresh();
            if (cannotDecline && getScope() === scope) el('status').textContent = t('nativeMedia.declineUnavailable');
        }
        else if (getScope() === scope && isActive()) {
            button.disabled = false; await refresh();
            if (cannotCancel && getScope() === scope) el('status').textContent = t('outbox.cannotCancel');
        }
    });
    function contextChanged() { stopLive(); if (isVisible()) refresh(); else reset(); }
    return { refresh, reset, disconnect, contextChanged };
}
