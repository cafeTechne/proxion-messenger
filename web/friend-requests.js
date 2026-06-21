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
        const fromShort = req.display_name || ((req.from_did || "unknown").slice(8, 22) + "…");
        const li = document.createElement("li");
        li.id = "fri-" + req.invitation_id;
        li.dataset.peerDid = req.from_did || "";
        li.style.cssText = "padding:6px 8px;background:#1e293b;border-radius:6px;margin:3px 0";
        li.innerHTML =
            `<div style="color:#e2e8f0;margin-bottom:4px">From <b>${escHtml(fromShort)}</b></div>` +
            `<div style="display:flex;gap:6px">` +
            `<button data-fr-action="accept" data-inv-id="${escHtml(req.invitation_id)}" ` +
            `style="background:#7c3aed;color:#fff;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:0.8em">Accept</button>` +
            `<button data-fr-action="dismiss" data-inv-id="${escHtml(req.invitation_id)}" ` +
            `style="background:#334155;color:#94a3b8;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:0.8em">Ignore</button>` +
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
