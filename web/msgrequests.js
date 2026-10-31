// msgrequests.js: message requests from browser-build (WebID) senders.
//
// On desktop, a signed and verified first DM from a WebID that is not a contact
// is not opened: it waits in the pod inbox while the user decides. The request
// shows the sender's claimed name (unverified) and their WebID, in the same
// sidebar list as friend requests. Accept adds the contact and opens the
// waiting messages; Decline blocks the sender, which deletes them unread.

import { escHtml } from './util.js';
import { t } from './i18n.js';

// A stable, id-safe key for a WebID (used in element ids).
export function requestKey(webid) {
    let h = 0;
    for (let i = 0; i < webid.length; i++) h = (Math.imul(31, h) + webid.charCodeAt(i)) | 0;
    return 'mrq-' + (h >>> 0).toString(36) + '-' + webid.length;
}

export function createMessageRequests({ onAccept, onDecline, doc } = {}) {
    const _pending = new Map();   // webid -> { from_webid, from_display_name }
    const _d = () => doc || (typeof document !== 'undefined' ? document : null);

    function _refreshSection() {
        const d = _d();
        const section = d && d.getElementById('friend-requests-section');
        const list = d && d.getElementById('friend-request-list');
        if (section) section.style.display = list && list.children.length ? '' : 'none';
    }

    function _render(req) {
        const d = _d();
        const list = d && d.getElementById('friend-request-list');
        const key = requestKey(req.from_webid);
        if (!list || d.getElementById(key)) return;
        const li = d.createElement('li');
        li.id = key;
        li.className = 'fr-card';
        li.dataset.mrWebid = req.from_webid;
        const name = req.from_display_name || '';
        li.innerHTML =
            `<div class="fr-card__name">${escHtml(t('mr.title'))}${name ? ` <b><bdi>${escHtml(name)}</bdi></b> <span class="fr-card__claim">${escHtml(t('fr.claimedUnverified'))}</span>` : ''}</div>` +
            `<div class="fr-card__did" title="${escHtml(req.from_webid)}">${escHtml(req.from_webid)}</div>` +
            `<div class="fr-card__actions">` +
            `<button data-mr-action="accept" class="btn btn--sm btn--accent">${escHtml(t('btn.accept'))}</button>` +
            `<button data-mr-action="decline" class="btn btn--sm btn--slate">${escHtml(t('mr.decline'))}</button>` +
            `</div>`;
        list.appendChild(li);
        _refreshSection();
    }

    // A request is shown once per sender, however many drops are waiting.
    function add(req) {
        if (!req || typeof req.from_webid !== 'string' || _pending.has(req.from_webid)) return false;
        _pending.set(req.from_webid, { from_webid: req.from_webid, from_display_name: req.from_display_name || '' });
        _render(_pending.get(req.from_webid));
        return true;
    }

    function remove(webid) {
        _pending.delete(webid);
        const d = _d();
        d?.getElementById(requestKey(webid))?.remove();
        _refreshSection();
    }

    function handleClick(e) {
        const btn = e.target && e.target.closest && e.target.closest('[data-mr-action]');
        if (!btn) return;
        const card = btn.closest('[data-mr-webid]');
        const webid = card && card.dataset.mrWebid;
        const req = webid && _pending.get(webid);
        if (!req) return;
        remove(webid);
        if (btn.dataset.mrAction === 'accept') onAccept?.(req);
        else onDecline?.(req);
    }

    return { add, remove, handleClick, has: (w) => _pending.has(w), list: () => [..._pending.values()] };
}
