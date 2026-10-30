// rooms.js — room command actions: leave/delete/transfer-ownership, kick a
// member, request the member list, the invite-copy helpers, and the join-room
// form submit.
//
// First slice of the rooms cluster: the command-style actions with low DOM
// coupling. The sidebar-rendering pieces (addRoomToSidebar / updateRoomPreview /
// showRoomMembers and its _membersRoomId, read across the dispatch) stay in
// main.js for a later slice.
//
// A factory. Reassignable host state (socket, activeView) is read live via
// getters. roomCreatorOf (a Set) and roomInviteUrls (an object) are host-owned
// shared state — read/written across the dispatch and renderer — injected by
// reference and never reassigned. showConfirm / showCopyModal are injected.
// Returned functions are destructured into same-named bindings in main.js.
import { t } from './i18n.js';
import { parseInvite } from './webjoin.js';

// Busy state for a submit button whose request waits on a gateway reply
// (room create / join). Marks the button aria-disabled and aria-busy with a
// pending label, and clears itself after timeoutMs if no reply ever arrives.
// aria-disabled rather than disabled: a disabled button loses focus, and the
// user just pressed this one, so focus would drop to <body>. A capture-phase
// click guard does the blocking instead (it also cancels Enter's implicit
// submit, which arrives as a click on the default button).
function _busyClickGuard(e) {
    if (e.currentTarget && e.currentTarget._busy) {
        e.preventDefault();
        e.stopImmediatePropagation();
    }
}

export function setButtonBusy(btn, busyLabel, timeoutMs = 15000) {
    if (!btn) return;
    if (btn._busyTimer) clearTimeout(btn._busyTimer);
    if (!btn._busy) btn._idleLabel = btn.textContent;
    btn._busy = true;
    if (!btn._busyGuarded && typeof btn.addEventListener === 'function') {
        btn.addEventListener('click', _busyClickGuard, true);
        btn._busyGuarded = true;
    }
    btn.setAttribute?.('aria-disabled', 'true');
    btn.setAttribute?.('aria-busy', 'true');
    btn.textContent = busyLabel;
    btn._busyTimer = setTimeout(() => clearButtonBusy(btn), timeoutMs);
}

export function clearButtonBusy(btn) {
    if (!btn || !btn._busy) return;
    clearTimeout(btn._busyTimer);
    btn._busyTimer = null;
    btn._busy = false;
    btn.removeAttribute?.('aria-disabled');
    btn.removeAttribute?.('aria-busy');
    btn.textContent = btn._idleLabel;
}

export function isButtonBusy(btn) {
    return !!(btn && btn._busy);
}

// A gateway (desktop) room code: 128 bits as unpadded base32.
const GATEWAY_CODE_RE = /^[a-z2-7]{26}$/i;

// The gateway command for what a user typed or pasted into a join box: a bare
// code or a link to this gateway joins here; a link to another gateway is
// handed to OUR gateway, which joins through the host and keeps the room here
// (the tab never leaves its own origin). Returns { error } for a bad link and
// null for empty input.
// The browser build (mode 'web') joins only browser-build invites (room id +
// owner WebID, from any origin) and the gateway only gateway rooms, so a link
// of the other kind is refused here with a message saying which it is.
export function inviteJoinCommand(raw, ownOrigin, mode = 'gateway') {
    const s = String(raw || "").trim();
    if (!s) return null;
    const webInvite = parseInvite(s);
    if (mode === 'web') {
        if (webInvite) return { cmd: "join_room", code: s };
        if (GATEWAY_CODE_RE.test(s)) return { error: 'join.desktopRoomInWeb' };
        if (!/^https?:\/\//i.test(s)) return { error: 'join.invalidInvite' };
        let url;
        try { url = new URL(s); } catch { return { error: 'room.invalidInviteLink' }; }
        return { error: url.searchParams.get("join") ? 'join.desktopRoomInWeb' : 'room.linkNoCode' };
    }
    if (webInvite) return { error: 'join.webRoomInDesktop' };
    if (!/^https?:\/\//i.test(s)) return { cmd: "join_room", code: s };
    let url;
    try { url = new URL(s); } catch { return { error: 'room.invalidInviteLink' }; }
    const code = url.searchParams.get("join");
    if (!code) return { error: 'room.linkNoCode' };
    if (url.origin === ownOrigin) return { cmd: "join_room", code };
    return { cmd: "join_remote_room", url: s };
}

export function createRooms({ getSocket, getActiveView, getRoomCreatorOf, getRoomInviteUrls, showConfirm, showCopyModal }) {

    function requestRoomMembers(roomId) {
        const socket = getSocket();
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ cmd: "get_room_members", room_id: roomId }));
        }
    }

    function leaveRoom() {
        const activeView = getActiveView();
        if (!activeView || activeView.type !== "local_room") return;
        const isOwner = getRoomCreatorOf().has(activeView.id);
        const msg = isOwner ? t('confirm.leaveRoom.ownerBody') : t('confirm.leaveRoom.body');
        showConfirm(msg, () => {
            getSocket().send(JSON.stringify({ cmd: "leave_local_room", room_id: activeView.id }));
        }, null, { title: t('confirm.leaveRoom.title'), confirmLabel: t('confirm.leaveRoom.verb'), danger: true });
    }

    function deleteRoom() {
        const activeView = getActiveView();
        if (!activeView || !getRoomCreatorOf().has(activeView.id)) return;
        showConfirm(t('confirm.deleteRoom'), () => {
            getSocket().send(JSON.stringify({ cmd: "delete_room", room_id: activeView.id }));
        }, null, { title: t('confirm.deleteRoom.title'), confirmLabel: t('confirm.deleteRoom.verb'), danger: true });
    }

    function transferOwnership(roomId, toDid) {
        const socket = getSocket();
        if (!socket || socket.readyState !== WebSocket.OPEN) return;
        socket.send(JSON.stringify({ cmd: "transfer_ownership", room_id: roomId, to_did: toDid }));
    }

    function copyRoomInviteFromModal() {
        const url = document.getElementById("room-invite-url").textContent;
        navigator.clipboard.writeText(url).then(() => {
            document.getElementById("room-invite-url").textContent = t('common.copied');
            setTimeout(() => { document.getElementById("room-invite-url").textContent = url; }, 1500);
        }).catch(() => {
            showCopyModal(url);
        });
    }

    function copyRoomInvite() {
        const activeView = getActiveView();
        const roomInviteUrls = getRoomInviteUrls();
        if (!activeView || !roomInviteUrls[activeView.id]) return;
        const url = roomInviteUrls[activeView.id];
        // Extract the raw code from the URL (?join=CODE) so users can share just the code
        const codeMatch = url.match(/[?&]join=([^&]+)/);
        const code = codeMatch ? codeMatch[1] : url;
        const urlEl = document.getElementById("invite-modal-url");
        const codeEl = document.getElementById("invite-modal-code");
        if (urlEl) urlEl.textContent = url;
        if (codeEl) codeEl.textContent = code;
        // Only the owner can replace the link (the gateway checks this too).
        // Pod-only rooms have no gateway invite to replace.
        const renewBtn = document.getElementById("invite-modal-renew");
        const onGateway = typeof WebSocket === 'function' && getSocket() instanceof WebSocket;
        if (renewBtn) renewBtn.hidden = !onGateway || !getRoomCreatorOf().has(activeView.id);
        document.getElementById("room-invite-modal").style.display = "flex";
    }

    function renewRoomInvite() {
        const activeView = getActiveView();
        const socket = getSocket();
        if (!activeView || !socket || socket.readyState !== WebSocket.OPEN) return;
        socket.send(JSON.stringify({ cmd: "new_room_invite", room_id: activeView.id }));
    }

    // The gateway replaced a room's invite link: remember it and, if the
    // invite dialog is showing that room, show the new link and code.
    function applyRenewedInvite(event) {
        getRoomInviteUrls()[event.room_id] = event.invite_url;
        const activeView = getActiveView();
        if (!activeView || activeView.id !== event.room_id) return;
        const urlEl = document.getElementById("invite-modal-url");
        const codeEl = document.getElementById("invite-modal-code");
        if (urlEl) urlEl.textContent = event.invite_url;
        if (codeEl) codeEl.textContent = event.code;
    }

    function _copyInviteText(text, btn) {
        navigator.clipboard.writeText(text).then(() => {
            const orig = btn.textContent;
            btn.textContent = t('common.copied');
            setTimeout(() => { btn.textContent = orig; }, 1500);
        }).catch(() => { showCopyModal(text); });
    }

    function kickMember(roomId, webid) {
        showConfirm(t('confirm.kickMember.body'), () => {
            const socket = getSocket();
            if (socket) socket.send(JSON.stringify({ cmd: "kick_member", room_id: roomId, webid: webid }));
        }, null, { title: t('confirm.kickMember.title'), confirmLabel: t('btn.kick'), danger: true });
    }

    function submitJoinRoom() {
        const raw = document.getElementById("join-room-input").value.trim();
        const errEl = document.getElementById("join-room-error");
        const submitBtn = document.getElementById("join-room-submit-btn");
        if (isButtonBusy(submitBtn)) return;
        const socket = getSocket();
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            errEl.textContent = t('conn.notConnectedGateway'); return;
        }
        if (!raw) { errEl.textContent = t('room.enterLinkOrCode'); return; }
        const cmd = inviteJoinCommand(raw, window.location.origin, window.proxionTransport?.mode);
        if (cmd.error) { errEl.textContent = t(cmd.error); return; }
        // Stay open until the gateway answers (room_joined / join_request_sent
        // close it, an error clears the busy state) so a second click cannot
        // send a duplicate join. Busy goes on BEFORE the send: the browser
        // build can answer within send() itself, and that reply must find the
        // button busy to clear it.
        errEl.textContent = "";
        setButtonBusy(submitBtn, t('room.joining'));
        socket.send(JSON.stringify(cmd));
    }

    return {
        requestRoomMembers, leaveRoom, deleteRoom, transferOwnership,
        copyRoomInviteFromModal, copyRoomInvite, _copyInviteText, kickMember, submitJoinRoom,
        renewRoomInvite, applyRenewedInvite,
    };
}
