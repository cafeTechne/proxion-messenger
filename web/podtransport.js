// podtransport.js — the gateway-less "socket" for Proxion Web (R102 Phase 1).
//
// A PodSocket looks like a WebSocket to main.js (readyState OPEN, send, close,
// onopen/onmessage/onclose) but backs the command protocol with pod.js instead
// of a gateway. In web mode this is installed as the app socket, so every
// existing `socket.send(...)` / `if (!socket)` path in main.js works unchanged.
//
// Phase 1 handles shared rooms only, and it can be small because most of the
// work is already client-side: the room_created handler writes the room to the
// pod, the send handler writes each message to the pod, and loadRoomHistory
// reads history straight from the pod. So the PodSocket only needs to:
//   register            -> emit `registered` (drives the app's post-auth init)
//   get_rooms           -> read owned room descriptors + joined-room records, emit `rooms`
//   chat_room_create    -> mint an id, list it in the type index, emit `room_created`
//   leave_local_room    -> forget a joined room, emit `left_room`
//   delete_room         -> unlist + delete an owned room, emit `room_deleted`
//   get_room_members    -> read the member list, emit `room_members`
//   send_room           -> emit the `message` echo (clears optimistic-pending)
//   get_dms             -> emit empty `dms` (no gateway DMs in web mode)
//   get_local_history   -> emit `local_history` from what this device holds
//   block / unblock / list_blocks -> the block list, kept in blockStore
//   (anything else)     -> ignored; UI gating hides those entry points
//
// DMs, presence, and calls arrive in R103–R105.

export const POD_SOCKET_OPEN = 1;
export const POD_SOCKET_CLOSED = 3;

// A room id that satisfies pod.js SAFE_ID_RE (/^[\w-]{1,128}$/).
export function genRoomId() {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
    return 'r' + Math.random().toString(36).slice(2) + Date.now().toString(36);
}

// Map pod room descriptors to the shape the `rooms` event handler expects. The
// invite link is rebuilt for each room we own, so it survives a reload.
export function roomsFromDescriptors(descs, selfWebId, makeRoomInvite = null) {
    return (descs || []).map((d) => ({
        id: d.room_id,
        name: d.title || d.room_id,
        invite_url: makeRoomInvite ? makeRoomInvite(d.room_id) : '',
        local: true,
        creator_webid: d.owner || selfWebId,
    }));
}

// Map joined-room records (rooms on someone else's pod) to `rooms` entries. The
// record rides along as `remote` so the handler can re-register the room.
export function roomsFromJoined(records) {
    return (records || []).map((r) => ({
        id: r.roomId,
        name: r.title || r.roomId,
        invite_url: '',
        local: true,
        creator_webid: r.owner,
        remote: r,
    }));
}

// Build a PodSocket. Dependencies are injected so this is unit-testable without
// a browser: getSelfWebId(), handleEvent(event), and a pod facade exposing
// podListOwnedRoomDescriptors(webId).
const _SIGNAL_CMDS = new Set(['voice_invite', 'voice_answer', 'ice_candidate', 'voice_hangup']);

import { parseInvite } from './webjoin.js';

export function createPodSocket({ getSelfWebId, handleEvent, pod, dm, calls, join, rooms, makeRoomInvite, history, blockStore }) {
    // Echo a just-sent DM back so its optimistic render is confirmed (pending
    // cleared). It carries no content: the payload is ciphertext, and the
    // plaintext optimistic copy already shows (main.js only confirms on
    // _podEcho, so the ciphertext can never replace it or reach the preview).
    function _echoDm(cmd) {
        handleEvent({
            type: 'message',
            message_id: cmd.message_id,
            thread_id: cmd.target_webid || cmd.cert_id,
            from_webid: getSelfWebId(),
            timestamp: new Date().toISOString(),
            source: 'local_dm',
            _podEcho: true,
        });
    }

    // The block list (web mode has no gateway list_blocks); stored by blockStore.
    function _blocks() {
        try { return (blockStore && blockStore.load()) || []; } catch { return []; }
    }
    function _saveBlocks(list) {
        try { if (blockStore) blockStore.save(list); } catch { /* storage unavailable */ }
    }

    async function route(cmd) {
        try {
            // WebRTC call signaling (R105): drop to the callee's pod call-inbox.
            if (cmd && _SIGNAL_CMDS.has(cmd.cmd)) {
                if (calls) await calls.sendSignal(cmd);
                return;
            }
            switch (cmd && cmd.cmd) {
                case 'local_dm':
                case 'send_dm': {
                    // main.js already ratchet-encrypted the payload; drop it into
                    // the recipient's pod. Echo (which clears the optimistic
                    // "pending" state) ONLY when the drop succeeded. A failed drop
                    // (e.g. the recipient has never opened Proxion, so their DM
                    // inbox does not exist yet) stays pending, and send-status.js
                    // marks it "Not delivered / Retry" instead of a silent success.
                    const okDm = dm ? await dm.dropDm(cmd) : true;
                    if (okDm) _echoDm(cmd);
                    break;
                }
                case 'send_dm_fanout': {
                    // Multi-device: one per-device envelope per recipient/own
                    // device. Echo only if at least one copy was dropped.
                    const okFan = (dm && dm.dropFanout) ? await dm.dropFanout(cmd) : true;
                    if (okFan) _echoDm({ message_id: cmd.message_id });
                    break;
                }
                case 'get_rooms': {
                    const self = getSelfWebId();
                    const descs = self ? await pod.podListOwnedRoomDescriptors(self) : [];
                    const owned = roomsFromDescriptors(descs, self, makeRoomInvite);
                    const ownedIds = new Set(owned.map((r) => r.id));
                    const joined = (self && rooms && rooms.listJoined) ? await rooms.listJoined() : [];
                    const others = roomsFromJoined(joined).filter((r) => !ownedIds.has(r.id));
                    handleEvent({ type: 'rooms', rooms: [...owned, ...others] });
                    break;
                }
                case 'chat_room_create': {
                    const roomId = genRoomId();
                    // List the room in our type index now, not on its first
                    // message: get_rooms lists from there, so an empty room would
                    // otherwise be gone after a reload.
                    if (pod.podRegisterRoomChat) await pod.podRegisterRoomChat(roomId).catch(() => {});
                    // The room_created handler writes the descriptor, ACL, members,
                    // and room index to the pod and opens the room. The invite link
                    // (room id + our WebID) lets someone request to join (R106).
                    handleEvent({
                        type: 'room_created',
                        room_id: roomId,
                        name: cmd.name,
                        code: roomId,
                        invite_url: makeRoomInvite ? makeRoomInvite(roomId) : '',
                    });
                    break;
                }
                case 'send_room': {
                    // The send handler already wrote this message to the pod; echo
                    // it back so the optimistic render is confirmed (pending cleared)
                    // and the pod order hint is stamped. Same-thread, so no unread.
                    // The sender draws and tracks the optimistic bubble right after
                    // this send returns, so the echo waits for that, or it would
                    // confirm nothing and the bubble would later fail.
                    await null;
                    handleEvent({
                        type: 'message',
                        message_id: cmd.message_id,
                        thread_id: cmd.room_id,
                        source: 'local_room',
                        from_webid: getSelfWebId(),
                        content: cmd.content,
                        timestamp: new Date().toISOString(),
                        local: true,
                    });
                    break;
                }
                case 'join_room': {
                    // The invite code is a web invite (room id + owner WebID).
                    // Drop a join request into the owner's pod; approval arrives async.
                    const inv = join && cmd.code ? parseInvite(cmd.code) : null;
                    if (inv) { await join.requestJoin(inv.roomId, inv.ownerWebId); handleEvent({ type: 'join_request_sent', room_id: inv.roomId, owner_webid: inv.ownerWebId }); }
                    else handleEvent({ type: 'join_invalid_invite' });
                    break;
                }
                case 'get_local_history': {
                    // History this device already holds (DM plaintext cache, the
                    // last pod read of a room). Always answer, even empty, so the
                    // loading skeleton clears; the pod read fills in the rest.
                    let messages = [];
                    try { messages = (history && await history(cmd.thread_id, cmd.limit)) || []; } catch { messages = []; }
                    handleEvent({ type: 'local_history', thread_id: cmd.thread_id, messages, last_read_ts: 0 });
                    break;
                }
                case 'block':
                case 'unblock': {
                    if (typeof cmd.webid !== 'string' || !cmd.webid) break;
                    const list = _blocks().filter((w) => w !== cmd.webid);
                    if (cmd.cmd === 'block') list.push(cmd.webid);
                    _saveBlocks(list);
                    break;
                }
                case 'list_blocks':
                    handleEvent({ type: 'blocks', webids: _blocks() });
                    break;
                case 'leave_local_room': {
                    // Only a joined room can be left here; the owner deletes instead.
                    if (rooms && rooms.leave && cmd.room_id && await rooms.leave(cmd.room_id)) {
                        handleEvent({ type: 'left_room', room_id: cmd.room_id });
                    }
                    break;
                }
                case 'delete_room': {
                    if (rooms && rooms.deleteOwned && cmd.room_id && await rooms.deleteOwned(cmd.room_id)) {
                        handleEvent({ type: 'room_deleted', room_id: cmd.room_id });
                    }
                    break;
                }
                case 'get_room_members': {
                    if (!rooms || !rooms.members || !cmd.room_id) break;
                    const members = await rooms.members(cmd.room_id);
                    if (members) handleEvent({ type: 'room_members', room_id: cmd.room_id, members, from_pod: true });
                    break;
                }
                case 'get_dms':
                    handleEvent({ type: 'dms', dms: [] });
                    handleEvent({ type: 'local_dms', dms: [] });
                    break;
                case 'join_remote_room':
                    // A link to a room on a gateway (Proxion desktop). The browser
                    // build has no gateway to join through: say so at once.
                    handleEvent({ type: 'join_invalid_invite', reason: 'desktop_room' });
                    break;
                default:
                    // Not supported in the Phase 1 web build.
                    break;
            }
        } catch (err) {
            console.warn('[Proxion] PodSocket route failed for', cmd && cmd.cmd, err);
        }
    }

    return {
        readyState: POD_SOCKET_OPEN,
        onopen: null,
        onmessage: null,
        onclose: null,
        send(str) {
            let cmd;
            try { cmd = JSON.parse(str); } catch { return; }
            route(cmd);   // fire-and-forget, like WebSocket.send
        },
        close() {
            this.readyState = POD_SOCKET_CLOSED;
            if (this.onclose) this.onclose();
        },
        _route: route,   // exposed for tests
    };
}
