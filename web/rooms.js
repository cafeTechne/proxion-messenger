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
        const msg = isOwner
            ? "Leave this room? As the owner, ownership will be transferred to another member, or the room will be deleted if you're the last member."
            : "Leave this room? You can rejoin with the invite link.";
        showConfirm(msg, () => {
            getSocket().send(JSON.stringify({ cmd: "leave_local_room", room_id: activeView.id }));
        });
    }

    function deleteRoom() {
        const activeView = getActiveView();
        if (!activeView || !getRoomCreatorOf().has(activeView.id)) return;
        showConfirm(t('confirm.deleteRoom'), () => {
            getSocket().send(JSON.stringify({ cmd: "delete_room", room_id: activeView.id }));
        });
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
        document.getElementById("room-invite-modal").style.display = "flex";
    }

    function _copyInviteText(text, btn) {
        navigator.clipboard.writeText(text).then(() => {
            const orig = btn.textContent;
            btn.textContent = t('common.copied');
            setTimeout(() => { btn.textContent = orig; }, 1500);
        }).catch(() => { showCopyModal(text); });
    }

    function kickMember(roomId, webid) {
        showConfirm(t('confirm.kickMember'), () => {
            const socket = getSocket();
            if (socket) socket.send(JSON.stringify({ cmd: "kick_member", room_id: roomId, webid: webid }));
        });
    }

    function submitJoinRoom() {
        const raw = document.getElementById("join-room-input").value.trim();
        const errEl = document.getElementById("join-room-error");
        const socket = getSocket();
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            errEl.textContent = t('conn.notConnectedGateway'); return;
        }
        if (!raw) { errEl.textContent = t('room.enterLinkOrCode'); return; }
        if (raw.startsWith("http://") || raw.startsWith("https://")) {
            try {
                const url = new URL(raw);
                const code = url.searchParams.get("join");
                const sameOrigin = url.origin === window.location.origin;
                if (sameOrigin && code) {
                    socket.send(JSON.stringify({ cmd: "join_room", code: code }));
                } else if (!sameOrigin) {
                    window.location.href = raw;
                    return;
                } else {
                    errEl.textContent = t('room.linkNoCode'); return;
                }
            } catch (e) { errEl.textContent = t('room.invalidUrl', { error: e.message }); return; }
        } else {
            socket.send(JSON.stringify({ cmd: "join_room", code: raw }));
        }
        document.getElementById("join-room-modal").style.display = "none";
    }

    return {
        requestRoomMembers, leaveRoom, deleteRoom, transferOwnership,
        copyRoomInviteFromModal, copyRoomInvite, _copyInviteText, kickMember, submitJoinRoom,
    };
}
