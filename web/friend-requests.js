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
        li.style.cssText = "padding:6px 8px;background:#1e293b;border-radius:6px;margin:3px 0";
        const nameLine = claimed
            ? `<div style="color:#e2e8f0;margin-bottom:2px">From <b>${escHtml(claimed)}</b> <span style="color:#f59e0b;font-size:0.72em">claimed name, unverified</span></div>`
            : `<div style="color:#e2e8f0;margin-bottom:2px">Friend request</div>`;
        li.innerHTML =
            nameLine +
            `<div style="color:var(--text-secondary);font-size:0.72em;margin-bottom:4px;word-break:break-all" title="${escHtml(fromDid)}">${escHtml(fromDid)}</div>` +
            `<div style="display:flex;gap:6px">` +
            `<button data-fr-action="accept" data-inv-id="${escHtml(req.invitation_id)}" ` +
            `style="background:#7c3aed;color:#fff;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:0.8em">Accept</button>` +
            `<button data-fr-action="dismiss" data-inv-id="${escHtml(req.invitation_id)}" ` +
            `style="background:#334155;color:var(--text-secondary);border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:0.8em">Ignore</button>` +
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
