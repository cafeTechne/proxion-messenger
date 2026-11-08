// Browser-specific cards share the requests sections with gateway/message
// requests, but only replace their own nodes. A friend is distinct from a DM.
import { t } from './i18n.js';
import { escHtml, dmFallbackName } from './util.js';
import { friendshipState } from './friendio.js';

export function createBrowserFriendsUi({ getController, openMessage, resolveName = dmFallbackName, showToast,
    confirmRemove, doc = document }) {
    let latest = { ready: false, rows: [] }, busySession = null;
    const element = id => doc.getElementById(id);
    const button = (action, label, peer, request = '') => `<button type="button" class="btn btn--sm btn--slate" data-bf-action="${action}" data-peer="${escHtml(peer)}" data-request="${escHtml(request)}">${escHtml(t(label))}</button>`;
    function section(id, listId) {
        const el = element(id), list = element(listId);
        if (el) el.style.display = list?.children.length ? '' : 'none';
    }
    function render(snapshot) {
        latest = snapshot;
        for (const id of ['friend-request-list', 'sent-request-list', 'browser-friends-list']) {
            element(id)?.querySelectorAll('[data-browser-friend]').forEach(node => node.remove());
        }
        const friendsSection = element('browser-friends-section');
        if (friendsSection) friendsSection.hidden = false;
        let count = 0;
        for (const row of snapshot.rows || []) {
            const state = friendshipState(row.mine, row.theirs), peer = row.peer;
            let list, actions = '', status = '';
            if (state === 'friends') {
                list = element('browser-friends-list'); count++;
                actions = button('message', 'btn.sendDm', peer) + button('remove', 'friends.remove', peer);
            } else if (state === 'incoming') {
                list = element('friend-request-list');
                actions = button('accept', 'btn.accept', peer, row.theirs.offer.id) + button('decline', 'btn.decline', peer, row.theirs.offer.id);
                status = t('friends.incoming');
            } else if (state === 'outgoing') {
                list = element('sent-request-list');
                actions = button('withdraw', 'fr.cancelRequest', peer, row.mine.offer.id);
                status = t(row.queued ? 'fr.waitingToDeliver' : 'fr.waitingForAnswer');
            }
            if (row.queued && row.mine && state !== 'outgoing') {
                // Keep a saved decline/removal visible until its notification lands.
                if (!list) list = element('sent-request-list');
                status = t('friends.syncPending');
            }
            if (row.queued) actions += button('retry', 'conn.retryNow', peer);
            if (!list) continue;
            const li = doc.createElement('li');
            li.className = 'fr-card'; li.dataset.browserFriend = peer;
            li.innerHTML = `<div class="fr-card__name"><bdi>${escHtml(resolveName(peer) || dmFallbackName(peer))}</bdi></div>`
                + `<div class="fr-card__did">${escHtml(peer)}</div>`
                + (status ? `<div class="fr-card__status">${escHtml(status)}</div>` : '')
                + (row.stale ? `<div class="fr-card__status">${escHtml(t('friends.stale'))}</div>` : '')
                + `<div class="fr-card__actions">${actions}</div>`;
            list.appendChild(li);
        }
        section('friend-requests-section', 'friend-request-list');
        section('sent-requests-section', 'sent-request-list');
        const status = element('browser-friends-status');
        if (status) status.textContent = !snapshot.ready ? t('common.loadingEllipsis') : snapshot.syncError ? t('friends.syncFailed') : count ? '' : t('friends.empty');
        const retry = element('browser-friends-retry');
        if (retry) { retry.hidden = !snapshot.syncError; retry.onclick = () => getController()?.refresh(); }
        for (const id of ['friend-request-list', 'sent-request-list', 'browser-friends-list']) {
            element(id)?.querySelectorAll('[data-bf-action]').forEach(el => { el.disabled = busySession !== null && busySession === getController()?.sessionId(); });
        }
    }
    async function handle(event) {
        const target = event.target.closest?.('[data-bf-action]');
        if (!target || target.disabled) return;
        const { bfAction: action, peer, request } = target.dataset;
        const controller = getController();
        if (!controller) return;
        const session = controller.sessionId();
        if (busySession === session) return;
        const current = () => controller === getController() && session === controller.sessionId();
        if (action === 'message') { openMessage(peer); return; }
        if (action === 'remove' && !(await confirmRemove(peer))) return;
        // An account switch while the dialog was open cannot act in the new one.
        if (!current()) return;
        busySession = session; render(latest);
        try {
            const result = action === 'retry' ? (await controller.refresh(), { ok: true }) : await controller[action]?.(peer, request);
            if (current() && !result?.ok) showToast(t(result?.reason === 'request_changed' ? 'friends.requestChanged' : 'friends.actionFailed'), 'error');
        } catch { if (current()) showToast(t('friends.actionFailed'), 'error'); }
        finally { if (busySession === session) busySession = null; if (current()) render(controller.view()); }
    }
    for (const id of ['friend-request-list', 'sent-request-list', 'browser-friends-list']) element(id)?.addEventListener('click', handle);
    return { render, review(peer) {
        const card = [...(element('friend-request-list')?.querySelectorAll('[data-browser-friend]') || [])].find(node => node.dataset.browserFriend === peer);
        card?.scrollIntoView?.({ block: 'nearest' }); card?.querySelector('button')?.focus();
    } };
}
