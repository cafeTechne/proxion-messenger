// Friend requests — render an incoming invite into the sidebar list, accept one
// (gateway command), and show/hide the section badge. No host mutable state.
//
// createFriendRequests({ getSocket }) — getSocket returns the reassignable host
// socket. escHtml imported from util.js.

import { escHtml } from './util.js';

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
        const nameLine = claimed
            ? `<div class="fr-card__name">From <b>${escHtml(claimed)}</b> <span class="fr-card__claim">claimed name, unverified</span></div>`
            : `<div class="fr-card__name">Friend request</div>`;
        li.innerHTML =
            nameLine +
            `<div class="fr-card__did" title="${escHtml(fromDid)}">${escHtml(fromDid)}</div>` +
            `<div class="fr-card__actions">` +
            `<button data-fr-action="accept" data-inv-id="${escHtml(req.invitation_id)}" ` +
            `class="btn btn--sm btn--accent">Accept</button>` +
            `<button data-fr-action="dismiss" data-inv-id="${escHtml(req.invitation_id)}" ` +
            `class="btn btn--sm btn--slate">Ignore</button>` +
            `</div>`;
        list.appendChild(li);
        refreshFriendRequestsBadge();
    }

    function acceptFriendRequest(invitationId) {
        const socket = getSocket();
        if (!socket || socket.readyState !== WebSocket.OPEN) return;
        socket.send(JSON.stringify({ cmd: "accept_friend_request", invitation_id: invitationId }));
    }

    return { renderPendingInvite, acceptFriendRequest, refreshFriendRequestsBadge };
}
