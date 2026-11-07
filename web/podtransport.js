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
//   kick_member         -> take a member out of an owned room, emit `member_kicked`
//   send_room           -> emit the `message` echo (clears optimistic-pending)
//   get_dms             -> emit empty `dms` (no gateway DMs in web mode)
//   get_local_history   -> emit `local_history` from what this device holds
//   block / unblock / list_blocks -> the block list, kept in blockStore
//   search              -> search loaded room text; main.js adds saved DMs
//   unsupported actions -> explicit error; gateway-only background sync is ignored
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
        invite_url: !d.closed && makeRoomInvite ? makeRoomInvite(d.room_id) : '',
        local: true,
        creator_webid: d.owner || selfWebId,
        ...(d.closed ? { closed: true } : {}),
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
// Shared startup, read markers and preference sync still send these gateway
// commands. Their browser behavior already lives in pod/UI code, so suppress
// only this explicit list rather than silently dropping every unknown action.
const _GATEWAY_BACKGROUND_CMDS = new Set([
    'register', 'get_identity', 'get_my_address', 'pod_status', 'link_pod',
    'set_presence', 'set_identity', 'set_receipts_enabled', 'set_link_previews_enabled',
    'get_peer_device_keys', 'get_relationships', 'list_friend_requests',
    'get_account_sync', 'set_account_pref', 'get_gateway_webid', 'get_inbox_webhook',
    'restore_contacts', 'rehost_room', 'set_thread_mute', 'update_last_read',
    'mark_read', 'typing', 'get_disappear_timer', 'get_room_roles',
    'get_voice_channel_state', 'get_message_readers', 'list_devices', 'list_sessions',
    'read_dm', 'read_room', 'subscribe_push', 'unsubscribe_push',
]);


import { parseInvite } from './webjoin.js';
import { isSealedCopy } from './dmtext.js';
import { isUndecryptable } from './e2e-secondary.js';

// Search snapshots supplied by the host, never an implicit global pod index.
// Later snapshots win, so the open room's edits/deletes replace stale cache
// entries. Only bounded, rendered plaintext is returned, without attachment bytes.
export function searchLoadedRooms(query, snapshots, limit = 50) {
    const words = String(query || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return [];
    const byRoom = new Map();
    for (const room of (Array.isArray(snapshots) ? snapshots : []).slice(-200)) {
        if (room && typeof room.id === 'string' && Array.isArray(room.messages)) byRoom.set(room.id, room);
    }
    const byId = new Map();
    for (const room of byRoom.values()) {
        for (const m of room.messages.slice(-1000)) {
            if (!m || typeof m.message_id !== 'string' || !m.message_id) continue;
            byId.set(JSON.stringify([room.id, m.message_id]), { room, message: m });
        }
    }
    const hits = [];
    for (const { room, message: m } of byId.values()) {
        if (m.deleted || isSealedCopy(m) || typeof m.content !== 'string' || isUndecryptable(m.content)) continue;
        const text = m.content.toLowerCase();
        if (!words.every((word) => text.includes(word))) continue;
        hits.push({
            message_id: m.message_id, thread_id: room.id, thread_name: room.name || '',
            content: m.content, from_webid: m.from_webid || '',
            from_display_name: m.from_display_name || '', timestamp: String(m.timestamp || ''),
        });
    }
    return hits.sort((a, b) => b.timestamp.localeCompare(a.timestamp))
        .slice(0, Math.max(0, Math.min(100, Number.isFinite(limit) ? Math.trunc(limit) : 50)));
}

export function createPodSocket({ getSelfWebId, handleEvent, pod, dm, calls, join, rooms, makeRoomInvite, history, blockStore, searchRooms }) {
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

    function fail(cmd, unsupported = false) {
        const event = {
            type: 'error', cmd: cmd?.cmd,
            code: unsupported ? 'E_UNSUPPORTED' : 'E_POD',
            message: unsupported ? 'transport_unsupported' : 'pod_operation_failed',
        };
        for (const key of ['message_id', 'thread_id', 'room_id', 'request_id']) {
            if (typeof cmd?.[key] === 'string') event[key] = cmd[key];
        }
        handleEvent(event);
    }

    async function route(cmd) {
        try {
            // WebRTC call signaling (R105): drop to the callee's pod call-inbox.
            if (cmd && _SIGNAL_CMDS.has(cmd.cmd)) {
                if (!calls) fail(cmd, true);
                else if (!(await calls.sendSignal(cmd))) fail(cmd);
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
                    if (!dm) { fail(cmd, true); break; }
                    if (await dm.dropDm(cmd)) _echoDm(cmd);
                    else fail(cmd);
                    break;
                }
                case 'send_dm_fanout': {
                    // Multi-device: one per-device envelope per recipient/own
                    // device. Echo only if at least one copy was dropped.
                    if (!dm?.dropFanout) { fail(cmd, true); break; }
                    if (await dm.dropFanout(cmd)) _echoDm({ message_id: cmd.message_id });
                    else fail(cmd);
                    break;
                }
                case 'get_rooms': {
                    const self = getSelfWebId();
                    const found = self ? await pod.podListOwnedRoomDescriptors(self) : [];
                    const descs = [];
                    for (const d of found) {
                        if (d.closed && d.deletion_complete) continue;
                        if (d.closed) {
                            let removed = false;
                            try { removed = !!(await rooms?.resumeDeletion?.(d.room_id)); } catch { /* retain owner retry entry */ }
                            if (removed) continue;
                            handleEvent({ type: 'error', cmd: 'delete_room', room_id: d.room_id, message: 'pod_operation_failed' });
                        }
                        descs.push(d);
                    }
                    const owned = roomsFromDescriptors(descs, self, makeRoomInvite);
                    const ownedIds = new Set(owned.map((r) => r.id));
                    const joined = (self && rooms && rooms.listJoined) ? await rooms.listJoined() : [];
                    const others = roomsFromJoined(joined).filter((r) => !ownedIds.has(r.id));
                    handleEvent({ type: 'rooms', rooms: [...owned, ...others] });
                    break;
                }
                case 'chat_room_create': {
                    const roomId = genRoomId();
                    // Production saves the descriptor, access rules and discovery
                    // record before the UI exposes an invite or allows a reload.
                    const saved = rooms?.createOwned
                        ? await rooms.createOwned(roomId, cmd.name)
                        : await pod.podRegisterRoomChat?.(roomId);
                    if (!saved) { handleEvent({ type: 'room_create_failed' }); break; }
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
                    if (inv) {
                        if (await join.requestJoin(inv.roomId, inv.ownerWebId)) handleEvent({ type: 'join_request_sent', room_id: inv.roomId, owner_webid: inv.ownerWebId });
                        else handleEvent({ type: 'join_invalid_invite', reason: 'unreachable' });
                    }
                    else handleEvent({ type: 'join_invalid_invite' });
                    break;
                }
                case 'search': {
                    const query = String(cmd.query || '').trim();
                    const event = { type: 'search_results', query, results: [], local_scope: true };
                    if (query.length > 500) event.error = 'pod_operation_failed';
                    else if (query.length >= 3 && searchRooms) {
                        try { event.results = (await searchRooms(query)) || []; }
                        catch { event.error = 'pod_operation_failed'; }
                    }
                    handleEvent(event);
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
                    if (!rooms?.leave) fail(cmd, true);
                    else if (cmd.room_id && await rooms.leave(cmd.room_id)) handleEvent({ type: 'left_room', room_id: cmd.room_id });
                    else fail(cmd);
                    break;
                }
                case 'delete_room': {
                    if (!rooms?.deleteOwned) fail(cmd, true);
                    else if (cmd.room_id && await rooms.deleteOwned(cmd.room_id)) handleEvent({ type: 'room_deleted', room_id: cmd.room_id });
                    else fail(cmd);
                    break;
                }
                case 'kick_member': {
                    // The owner takes someone out of the room's ACL; their next
                    // append is refused and their app shows the removal.
                    if (!rooms?.kick) fail(cmd, true);
                    else if (cmd.room_id && typeof cmd.webid === 'string' && await rooms.kick(cmd.room_id, cmd.webid)) {
                        handleEvent({ type: 'member_kicked', room_id: cmd.room_id, webid: cmd.webid });
                    } else fail(cmd);
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
                    if (typeof cmd?.cmd === 'string' && !_GATEWAY_BACKGROUND_CMDS.has(cmd.cmd)) fail(cmd, true);
                    break;
            }
        } catch (err) {
            console.warn('[Proxion] PodSocket route failed for', cmd && cmd.cmd, err);
            if (cmd?.cmd === 'chat_room_create') handleEvent({ type: 'room_create_failed' });
            else if (cmd?.cmd === 'join_room') handleEvent({ type: 'join_invalid_invite', reason: 'unreachable' });
            else fail(cmd);
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
