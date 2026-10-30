// Friend requests — render an incoming invite into the sidebar list, accept one
// (gateway command), and show/hide the section badge. No host mutable state.
//
// createFriendRequests({ getSocket }) — getSocket returns the reassignable host
// socket. escHtml imported from util.js.

import { escHtml } from './util.js';
import { t } from './i18n.js';

export function createFriendRequests({ getSocket }) {
    function refreshFriendRequestsBadge() {
        const section = document.getElementById("friend-requests-section");
        const list = document.getElementById("friend-request-list");
        if (section) section.style.display = list && list.children.length ? "" : "none";
    }

    function renderPendingInvite(req) {
        const list = document.getElementById("friend-request-list");
        if (!list || document.getElementById("fri-" + req.invitation_id)) return;
        // The display name is requester-chosen and self-signed, so it can copy a
        // trusted contact's name. Accepting binds a certificate to the sender's
        // key, so surface the stable identifier (from_did) inline and mark the
        // name as an unverified claim.
        const claimed = req.display_name || "";
        const fromDid = req.from_did || "unknown";
        const li = document.createElement("li");
        li.id = "fri-" + req.invitation_id;
        li.dataset.peerDid = req.from_did || "";
        li.className = "fr-card";
        // "From {name}" is translated as one phrase (word order differs by
        // language); the bold name is spliced in at the placeholder, and <bdi>
        // keeps a right-to-left name from reordering the words around it.
        const [before, after = ""] = t('fr.from', { name: "\u0000" }).split("\u0000");
        const nameLine = claimed
            ? `<div class="fr-card__name">${escHtml(before)}<b><bdi>${escHtml(claimed)}</bdi></b>${escHtml(after)} <span class="fr-card__claim">${escHtml(t('fr.claimedUnverified'))}</span></div>`
            : `<div class="fr-card__name">${escHtml(t('fr.title'))}</div>`;
        li.innerHTML =
            nameLine +
            `<div class="fr-card__did" title="${escHtml(fromDid)}">${escHtml(fromDid)}</div>` +
            `<div class="fr-card__actions">` +
            `<button data-fr-action="accept" data-inv-id="${escHtml(req.invitation_id)}" ` +
            `class="btn btn--sm btn--accent">${escHtml(t('btn.accept'))}</button>` +
            `<button data-fr-action="dismiss" data-inv-id="${escHtml(req.invitation_id)}" ` +
            `class="btn btn--sm btn--slate">${escHtml(t('fr.ignore'))}</button>` +
            `</div>`;
        list.appendChild(li);
        refreshFriendRequestsBadge();
    }

    // button: the card's Accept button, disabled while the accept is in flight
    // so a second click cannot send a second accept. The host re-enables it
    // when the gateway refuses. Returns whether the accept was sent.
    function acceptFriendRequest(invitationId, button = null) {
        if (button?.disabled) return false;
        const socket = getSocket();
        if (!socket || socket.readyState !== WebSocket.OPEN) return false;
        socket.send(JSON.stringify({ cmd: "accept_friend_request", invitation_id: invitationId }));
        if (button) button.disabled = true;
        return true;
    }

    return { renderPendingInvite, acceptFriendRequest, refreshFriendRequestsBadge };
}
