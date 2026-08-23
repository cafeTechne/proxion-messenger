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
//   get_rooms           -> read owned room descriptors, emit `rooms`
//   chat_room_create    -> mint an id, emit `room_created` (handler persists)
//   send_room           -> emit the `message` echo (clears optimistic-pending)
//   get_dms             -> emit empty `dms` (no gateway DMs in web mode)
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

// Map pod room descriptors to the shape the `rooms` event handler expects.
export function roomsFromDescriptors(descs, selfWebId) {
    return (descs || []).map((d) => ({
        id: d.room_id,
        name: d.title || d.room_id,
        invite_url: '',
        local: true,
        creator_webid: d.owner || selfWebId,
    }));
}

// Build a PodSocket. Dependencies are injected so this is unit-testable without
// a browser: getSelfWebId(), handleEvent(event), and a pod facade exposing
// podListOwnedRoomDescriptors(webId).
export function createPodSocket({ getSelfWebId, handleEvent, pod, dm }) {
    // Echo a just-sent DM back so its optimistic render is confirmed (pending
    // cleared) and dedups by message_id. No `local` flag: the ciphertext content
    // must not reach the DM preview; the plaintext optimistic copy already shows.
    function _echoDm(cmd) {
        handleEvent({
            type: 'message',
            message_id: cmd.message_id,
            thread_id: cmd.target_webid || cmd.cert_id,
            from_webid: getSelfWebId(),
            content: cmd.content,
            timestamp: new Date().toISOString(),
            source: 'local_dm',
        });
    }

    async function route(cmd) {
        try {
            switch (cmd && cmd.cmd) {
                case 'local_dm':
                case 'send_dm': {
                    // main.js already ratchet-encrypted the payload; drop it into
                    // the recipient's pod, then confirm the optimistic render.
                    if (dm) await dm.dropDm(cmd);
                    _echoDm(cmd);
                    break;
                }
                case 'get_rooms': {
                    const self = getSelfWebId();
                    const descs = self ? await pod.podListOwnedRoomDescriptors(self) : [];
                    handleEvent({ type: 'rooms', rooms: roomsFromDescriptors(descs, self) });
                    break;
                }
                case 'chat_room_create': {
                    const roomId = genRoomId();
                    // The room_created handler writes the descriptor, ACL, members,
                    // and room index to the pod and opens the room.
                    handleEvent({
                        type: 'room_created',
                        room_id: roomId,
                        name: cmd.name,
                        code: roomId,
                        invite_url: '',
                    });
                    break;
                }
                case 'send_room': {
                    // The send handler already wrote this message to the pod; echo
                    // it back so the optimistic render is confirmed (pending cleared)
                    // and the pod order hint is stamped. Same-thread, so no unread.
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
                case 'get_dms':
                    handleEvent({ type: 'dms', dms: [] });
                    handleEvent({ type: 'local_dms', dms: [] });
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
