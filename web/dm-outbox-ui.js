import { escHtml, formatTimestamp } from './util.js';
import { t } from './i18n.js';

// A device-local review of saved sends. No message body or crypto details enter
// this view, and actions always revalidate their account/session context.
export function createDmOutboxUi({ outbox, getScope, isActive, getLegacy = () => [], reviewLegacy, reviewUnknown = () => {}, reviewMedia = () => {},
    getName = id => id, doc = document }) {
    const el = suffix => doc.getElementById('dm-outbox-' + suffix);
    let generation = 0, displayedScope = null;
    const states = () => ({ nativeBlocked: t('nativeMedia.blocked'), nativeWaiting: t('nativeMedia.waiting'), nativePeer: t('nativeMedia.peerUnavailable'),
        nativeHeader: t('nativeMedia.headerWaiting'), nativeFile: t('nativeMedia.fileWaiting'),
        nativeHistory: t('nativeMedia.historyRequired'), nativeKey: t('nativeMedia.keyChanged'), waiting: t('outbox.waiting'), confirmation: t('outbox.confirmation'),
        cleanup: t('outbox.cleanup'), account: t('outbox.originalAccount'), accountConfirmed: t('outbox.confirmedAccount'), unbound: t('outbox.unknownAccount'), media: t('dmMedia.finishHistory'), mediaAttention: t('dmMedia.originalAccount'), sync: t('outbox.sync'), syncAttention: t('outbox.syncAttention'), attention: t('outbox.attention'), storage: t('outbox.storage'), legacy: t('outbox.legacy') });
    function reset() { generation++; displayedScope = null; el('rows')?.replaceChildren(); if (el('status')) el('status').textContent = ''; }
    async function refresh() {
        reset();
        const token = generation, scope = getScope();
        const current = () => token === generation && scope === getScope() && isActive();
        if (!current()) { el('status').textContent = t('outbox.unavailable'); return; }
        el('status').textContent = t('outbox.loading');
        try {
            const pending = await outbox.inspect();
            const legacy = getLegacy().map(row => ({ id: row.message_id, peer: row.thread_id, timestamp: row.timestamp, state: 'legacy', canCancel: false }));
            if (!current()) return;
            displayedScope = scope;
            const rows = [...pending, ...legacy];
            el('status').textContent = rows.length ? t('outbox.uncertain') : t('outbox.empty');
            el('rows').innerHTML = rows.map(row => `<li><p>${escHtml(getName(row.peer))} · ${escHtml(formatTimestamp(row.timestamp))}</p>`
                + `<p>${escHtml(states()[row.state] || states().waiting)}</p>`
                + (row.state === 'cleanup' ? '' : `<button type="button" class="btn btn--sm" data-outbox-id="${escHtml(row.id)}" data-outbox-action="${row.state === 'legacy' ? 'review' : row.state === 'unbound' ? 'unknown' : row.state === 'media' ? 'media' : 'retry'}"${((row.state === 'nativeBlocked' || row.state === 'attention' || row.state === 'syncAttention' || row.state === 'mediaAttention' || row.state === 'account' || row.state === 'accountConfirmed') && !row.canRetry) ? ' disabled' : ''}>${escHtml((row.state === 'legacy' || row.state === 'unbound') ? t('outbox.review') : row.state === 'media' ? t('dmMedia.openConversation') : t('send.retry'))}</button>`)
                + (row.canCancel ? ` <button type="button" class="btn btn--sm" data-outbox-id="${escHtml(row.id)}" data-outbox-action="cancel">${escHtml(t('outbox.cancel'))}</button>` : '') + '</li>').join('');
        } catch { if (current()) el('status').textContent = t('outbox.unavailable'); }
    }
    el('refresh')?.addEventListener('click', refresh);
    el('rows')?.addEventListener('click', async event => {
        const button = event.target.closest('[data-outbox-action]');
        if (!button || button.disabled || !isActive()) return;
        if (displayedScope !== getScope()) { reset(); return; }
        const scope = getScope(), id = button.dataset.outboxId, action = button.dataset.outboxAction;
        button.disabled = true;
        let cannotCancel = false;
        try {
            if (action === 'review') await reviewLegacy(id);
            else if (action === 'unknown') await reviewUnknown(id);
            else if (action === 'media') await reviewMedia(id);
            else if (action === 'cancel') {
                cannotCancel = !await outbox.cancel(id);
            } else await outbox.send(id);
        } catch { if (getScope() === scope) el('status').textContent = t('outbox.unavailable'); }
        if (getScope() === scope && isActive() && action !== 'cancel') await refresh();
        else if (getScope() === scope && isActive()) {
            button.disabled = false; await refresh();
            if (cannotCancel && getScope() === scope) el('status').textContent = t('outbox.cannotCancel');
        }
    });
    return { refresh, reset };
}
