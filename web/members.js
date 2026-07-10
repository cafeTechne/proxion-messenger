// Room members panel — toggle visibility (mobile drawer vs. desktop column),
// render the online/offline grouped roster. No host mutable state.
//
// createMembers({ getActiveView, requestRoomMembers }) — getActiveView returns
// the reassignable host activeView; requestRoomMembers asks the gateway for the
// current roster (lives in rooms.js).

import { escHtml, webidColor } from './util.js';

export function createMembers({ getActiveView, requestRoomMembers }) {
    function toggleMembersPanel() {
        const panel = document.getElementById("members-panel");
        const activeView = getActiveView();
        const isMobile = window.innerWidth <= 768;
        if (isMobile) {
            const isOpen = panel.classList.contains("mobile-open");
            panel.classList.toggle("mobile-open", !isOpen);
            if (!isOpen && activeView) requestRoomMembers(activeView.id);
        } else {
            const isShown = panel.style.display === "block";
            panel.style.display = isShown ? "none" : "block";
            if (!isShown && activeView) requestRoomMembers(activeView.id);
        }
    }

    function memberHtml(m) {
        const color = webidColor(m.webid);
        const displayName = m.display_name || m.webid || "?";
        const initial = escHtml(displayName[0].toUpperCase());
        const presenceClass = m.status === "online" ? "online" : m.status === "away" ? "away" : m.status === "busy" ? "busy" : "";
        const fedBadge = m.federated
            ? `<span title="Federated member (${escHtml(m.gateway || 'remote gateway')})" style="font-size:0.65em;color:#8091a7;margin-left:4px;vertical-align:middle;">&#x1F517;</span>`
            : "";
        return `<div class="member-item" data-msg-action="profile" data-webid="${escHtml(m.webid)}" data-name="${escHtml(displayName)}">
                <div style="position:relative;display:inline-block;margin-right:8px;">
                    <div class="avatar placeholder" style="background:${color};width:28px;height:28px;line-height:28px;font-size:12px;font-weight:bold;text-align:center;">${initial}</div>
                    <div class="avatar-presence ${presenceClass}" title="${escHtml(m.status || '')}"></div>
                </div>
                <span>${escHtml(m.display_name || m.webid.slice(0, 12))}${fedBadge}</span>
                <span class="sr-only">, ${escHtml(m.status || "offline")}${m.federated ? ", federated" : ""}</span>
            </div>`;
    }

    function renderMembersPanel(members) {
        const list = document.getElementById("members-list");
        const online = members.filter(m => m.status === "online");
        const offline = members.filter(m => m.status !== "online");
        list.innerHTML = "";
        if (online.length) {
            list.innerHTML += `<div class="members-section-header">Online — ${online.length}</div>`;
            online.forEach(m => list.innerHTML += memberHtml(m));
        }
        if (offline.length) {
            list.innerHTML += `<div class="members-section-header">Offline — ${offline.length}</div>`;
            offline.forEach(m => list.innerHTML += memberHtml(m));
        }
    }

    return { toggleMembersPanel, memberHtml, renderMembersPanel };
}
