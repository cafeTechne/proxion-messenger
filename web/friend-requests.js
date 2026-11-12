// Friend requests — render an incoming invite into the sidebar list, accept one
// (gateway command), and show/hide the section badge. Also the requests we sent
// that are still waiting, each with a Cancel button.
//
// createFriendRequests({ getSocket }) — getSocket returns the reassignable host
// socket. escHtml imported from util.js.

import { escHtml } from './util.js';
import { t } from './i18n.js';

export function createFriendRequests({ getSocket }) {
    function markAcceptancePending(invitationId, retryable = true) {
        const row = document.getElementById('fri-' + invitationId);
        if (!row) return;
        row.dataset.accepting = "true";
        const status = row.querySelector?.('[data-fr-acceptance]');
        if (status) { status.hidden = false; status.textContent = t('fr.acceptancePending'); }
        const accept = row.querySelector?.('[data-fr-action="accept"]');
        if (accept) { accept.textContent = t('btn.retry'); accept.disabled = !retryable; }
        const dismiss = row.querySelector?.('[data-fr-action="dismiss"]');
        if (dismiss) dismiss.disabled = true;
    }
    function refreshFriendRequestsBadge() {
        const section = document.getElementById("friend-requests-section");
        const list = document.getElementById("friend-request-list");
        if (section) section.style.display = list && list.children.length ? "" : "none";
    }

    function renderPendingInvite(req) {
        const list = document.getElementById("friend-request-list");
        if (!list) return;
        if (document.getElementById("fri-" + req.invitation_id)) {
            if (req.status === 'accepting') markAcceptancePending(req.invitation_id);
            return;
        }
        // The display name is requester-chosen and self-signed, so it can copy a
        // trusted contact's name. Accepting binds a certificate to the sender's
        // key, so surface the stable identifier (from_did) inline and mark the
        // name as an unverified claim.
        const claimed = req.display_name || "";
        const fromDid = req.from_did || "unknown";
        const li = document.createElement("li");
        li.id = "fri-" + req.invitation_id;
        li.dataset.peerDid = req.from_did || "";
        if (req.status === "accepting") li.dataset.accepting = "true";
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
            `<div class="fr-card__status" data-fr-acceptance role="status"${req.status === 'accepting' ? '' : ' hidden'}>${req.status === 'accepting' ? escHtml(t('fr.acceptancePending')) : ''}</div>` +
            `<div class="fr-card__actions">` +
            `<button data-fr-action="accept" data-inv-id="${escHtml(req.invitation_id)}" ` +
            `class="btn btn--sm btn--accent">${escHtml(t(req.status === 'accepting' ? 'btn.retry' : 'btn.accept'))}</button>` +
            `<button data-fr-action="dismiss" data-inv-id="${escHtml(req.invitation_id)}" ` +
            `class="btn btn--sm btn--slate"${req.status === 'accepting' ? ' disabled' : ''}>${escHtml(t('fr.ignore'))}</button>` +
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
        const dismiss = button?.closest?.('li')?.querySelector('[data-fr-action="dismiss"]');
        if (dismiss) dismiss.disabled = true;
        return true;
    }

    // Requests we sent that are still waiting, by invitation_id. A card is
    // rebuilt from its entry when its delivery state changes.
    const sentRequests = new Map();

    function refreshSentRequests() {
        const section = document.getElementById("sent-requests-section");
        if (section) section.style.display = sentRequests.size ? "" : "none";
    }

    function sentCardHtml(req) {
        const target = req.target_did || "";
        const name = req.display_name || "";
        const [before, after = ""] = t('fr.to', { name: "\u0000" }).split("\u0000");
        const nameLine = name
            ? `<div class="fr-card__name">${escHtml(before)}<b><bdi>${escHtml(name)}</bdi></b>${escHtml(after)}</div>`
            : `<div class="fr-card__name">${escHtml(t('fr.sentTitle'))}</div>`;
        const status = t(req.queued ? 'fr.waitingToDeliver' : 'fr.waitingForAnswer');
        return nameLine +
            `<div class="fr-card__did" title="${escHtml(target)}">${escHtml(target)}</div>` +
            `<div class="fr-card__status">${escHtml(status)}</div>` +
            `<div class="fr-card__actions">` +
            `<button data-fr-action="withdraw" data-inv-id="${escHtml(req.invitation_id)}" ` +
            `class="btn btn--sm btn--slate">${escHtml(t('fr.cancelRequest'))}</button>` +
            `</div>`;
    }

    // Add a sent request, or update one already shown (merging the fields).
    function renderSentRequest(req) {
        if (!req?.invitation_id) return;
        const list = document.getElementById("sent-request-list");
        if (!list) return;
        const merged = { ...(sentRequests.get(req.invitation_id) || {}), ...req };
        sentRequests.set(req.invitation_id, merged);
        let li = document.getElementById("frs-" + req.invitation_id);
        if (!li) {
            li = document.createElement("li");
            li.id = "frs-" + req.invitation_id;
            li.className = "fr-card";
            list.appendChild(li);
        }
        li.dataset.peerDid = merged.target_did || "";
        li.innerHTML = sentCardHtml(merged);
        refreshSentRequests();
    }

    // The gateway's full list of sent requests still waiting.
    function setSentRequests(reqs) {
        const keep = new Set((reqs || []).map(r => r.invitation_id));
        for (const iid of [...sentRequests.keys()]) {
            if (!keep.has(iid)) removeSentRequest(iid);
        }
        (reqs || []).forEach(renderSentRequest);
        refreshSentRequests();
    }

    // The peer's gateway now has it: it is waiting for their answer.
    function markSentDelivered(invitationId) {
        if (sentRequests.has(invitationId)) renderSentRequest({ invitation_id: invitationId, queued: false });
    }

    function removeSentRequest(invitationId) {
        sentRequests.delete(invitationId);
        document.getElementById("frs-" + invitationId)?.remove();
        refreshSentRequests();
    }

    function hasSentRequest(invitationId) {
        return sentRequests.has(invitationId);
    }

    // Cancel a sent request. The card goes when the gateway confirms
    // (friend_request_withdrawn); until then the button stays disabled.
    function withdrawFriendRequest(invitationId, button = null) {
        if (button?.disabled) return false;
        const socket = getSocket();
        if (!socket || socket.readyState !== WebSocket.OPEN) return false;
        socket.send(JSON.stringify({ cmd: "withdraw_friend_request", invitation_id: invitationId }));
        if (button) button.disabled = true;
        const dismiss = button?.closest?.('li')?.querySelector('[data-fr-action="dismiss"]');
        if (dismiss) dismiss.disabled = true;
        return true;
    }

    return {
        renderPendingInvite, acceptFriendRequest, refreshFriendRequestsBadge, markAcceptancePending,
        renderSentRequest, setSentRequests, markSentDelivered, removeSentRequest,
        hasSentRequest, withdrawFriendRequest,
    };
}
