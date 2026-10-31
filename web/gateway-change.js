// gateway-change.js: a contact was seen at a new gateway address that their own
// key did not vouch for. The gateway keeps sending to the old address until the
// user approves the move, so a stranger cannot redirect a contact's messages.
// This keeps the pending list from the gateway, shows the change on that
// contact's DM, and sends the approve / reject decision.

import { t } from './i18n.js';

export function createGatewayChanges({ getSocket, showConfirm, showToast, getActivePeer, doc = document }) {
    const pending = new Map();      // peer did -> { peer_did, display_name, old_url, new_url }
    const announced = new Set();    // peers already toasted on this page

    function _nameOf(c) {
        return c.display_name || (c.peer_did.slice(0, 20) + '…');
    }

    function _send(cmd, peerDid) {
        const s = getSocket();
        if (s?.readyState === WebSocket.OPEN) s.send(JSON.stringify({ cmd, peer_did: peerDid }));
    }

    function get(peerDid) { return (peerDid && pending.get(peerDid)) || null; }

    // The gateway's peer_gateway_changes list is authoritative.
    function handleChangesEvent(changes) {
        pending.clear();
        for (const c of Array.isArray(changes) ? changes : []) {
            if (!c || typeof c.peer_did !== 'string' || !c.peer_did) continue;
            pending.set(c.peer_did, {
                peer_did: c.peer_did,
                display_name: typeof c.display_name === 'string' ? c.display_name : '',
                old_url: String(c.old_url || ''),
                new_url: String(c.new_url || ''),
            });
        }
        for (const [did, c] of pending) {
            if (announced.has(did)) continue;
            announced.add(did);
            showToast?.(t('gwchange.toast', { name: _nameOf(c) }));
        }
        renderBar(getActivePeer?.());
    }

    function approve(peerDid) {
        const c = get(peerDid);
        if (!c) return;
        showConfirm(t('gwchange.confirm.body', { name: _nameOf(c), url: c.new_url }),
            () => _send('approve_peer_gateway_change', peerDid), null,
            { title: t('gwchange.confirm.title'), confirmLabel: t('gwchange.approve'), danger: true });
    }

    function reject(peerDid) {
        if (get(peerDid)) _send('reject_peer_gateway_change', peerDid);
    }

    function handleResult(event) {
        if (event?.type === 'peer_gateway_change_approved' && event.approved) {
            showToast?.(t('gwchange.approved'));
        } else if (event?.type === 'peer_gateway_change_rejected' && event.rejected) {
            showToast?.(t('gwchange.rejected'));
        }
    }

    function _urlRow(label, url) {
        const row = doc.createElement('div');
        row.className = 'gateway-change-bar__row';
        const k = doc.createElement('span');
        k.className = 'gateway-change-bar__label';
        k.textContent = label;
        const v = doc.createElement('code');
        v.className = 'gateway-change-bar__url';
        v.textContent = url;
        row.append(k, v);
        return row;
    }

    // The notice on the open DM, or nothing when that contact has no pending change.
    function renderBar(peerDid) {
        const bar = doc.getElementById('gateway-change-bar');
        if (!bar) return;
        const c = get(peerDid);
        bar.replaceChildren();
        if (!c) { bar.hidden = true; return; }
        const title = doc.createElement('p');
        title.className = 'gateway-change-bar__title';
        title.textContent = t('gwchange.notice', { name: _nameOf(c) });
        const warn = doc.createElement('p');
        warn.className = 'gateway-change-bar__warning';
        warn.textContent = t('gwchange.warning', { name: _nameOf(c) });
        const actions = doc.createElement('div');
        actions.className = 'gateway-change-bar__actions';
        const ok = doc.createElement('button');
        ok.type = 'button';
        ok.className = 'btn btn--slate btn--sm';
        ok.dataset.action = 'approve';
        ok.textContent = t('gwchange.approve');
        ok.addEventListener('click', () => approve(c.peer_did));
        const no = doc.createElement('button');
        no.type = 'button';
        no.className = 'btn btn--outline btn--sm';
        no.dataset.action = 'reject';
        no.textContent = t('gwchange.reject');
        no.addEventListener('click', () => reject(c.peer_did));
        actions.append(ok, no);
        bar.append(title, _urlRow(t('gwchange.oldUrl'), c.old_url),
            _urlRow(t('gwchange.newUrl'), c.new_url), warn, actions);
        bar.hidden = false;
    }

    function requestChanges() {
        const s = getSocket();
        if (s?.readyState === WebSocket.OPEN) s.send(JSON.stringify({ cmd: 'get_peer_gateway_changes' }));
    }

    return { handleChangesEvent, handleResult, renderBar, approve, reject, get, requestChanges };
}
