// crossrooms.js: desktop pod rooms, shared with browser-build users.
//
// The one room format both builds speak is a pod room: a Solid Long Chat in its
// owner's pod that members append to (webrooms.js, podroomio.js). The desktop
// client takes part as the WebID of the pod its gateway is connected to, over
// the gateway's pod bridge (gwpod.js):
//   - joining a room hosted in a browser-build user's pod, from their invite
//     link (the join-inbox handshake in webjoin.js); after the approval the
//     room's container is registered with the gateway, the only foreign place
//     it lets the pod credentials reach (DPoP, GET/HEAD/PATCH on chat files);
//   - hosting a room in our own pod that browser-build users join with a link
//     to the public web build, approving their requests here.
// Messages are signed by this device's key with maker = our WebID, so readers
// on either side verify them against our published signer.json.
//
// Gateway rooms are untouched: only commands for a pod room are routed here.
// Dependencies are injected so main.js stays the composition root.

import { createPodRoomIo } from './podroomio.js';
import { createWebJoin, makeInvite, parseInvite } from './webjoin.js';
import { createRoomSync, createJoinedRoomStore, withJoinedRecord, withoutJoinedRecord } from './webrooms.js';
import { createRoomAccessMonitor } from './room-access.js';
import { createOwnerMembership } from './room-membership.js';
import { peerPodRootFromWebId } from './webdm.js';
import { isPeerPodRootAllowed } from './ssrf.js';
import { chatRootUrl, dayFileAt } from './longchat.js';
import { buildRoomDescriptor, withMembers } from './roomdesc.js';
import { genRoomId } from './podtransport.js';

// Where browser-build users open an invite. Never the loopback gateway: the
// person invited has no gateway. Overridable for a self-hosted copy (and smokes).
export const WEB_APP_URL = 'https://cafetechne.github.io/proxion-messenger/app/';
export function webAppUrl(storage) {
    try {
        const v = storage && storage.getItem('proxion_web_app_url');
        if (v && /^https:\/\//.test(v)) return v;
    } catch { /* no storage */ }
    return WEB_APP_URL;
}

// The container of a room joined from an approval, or null. owner_pod_root
// rides in an attacker-writable join drop, so it must pass the SSRF gate and
// be the root derived from the approver's WebID; the container is always the
// one derived from both (the gateway registers only that one).
export function vetJoinedRoom(roomId, ownerWebId, ownerPodRoot, ownRoot) {
    if (!roomId || !/^[\w-]{1,128}$/.test(roomId) || !ownerWebId || !ownerPodRoot) return null;
    if (!isPeerPodRootAllowed(ownerPodRoot, ownRoot)) return null;
    if (peerPodRootFromWebId(ownerWebId) !== ownerPodRoot) return null;
    return chatRootUrl(ownerPodRoot, roomId);
}

const _ROOM_KEYS = ['room_id', 'thread_id'];

export function createCrossRooms({
    bridge, send, handleEvent, getState, getSelfId, getDisplayName,
    getSigner, signDescriptor = async (d) => d, askApprove,
    onRoom = () => {}, onRemoved = () => {}, onMessages = () => {}, notify = () => {},
    onReset = () => {},
    onJoinStatus = () => {},
    inviteBase = () => WEB_APP_URL,
    timeoutMs = 15000,
    storage = globalThis.localStorage,
    accessPollMs = 15000, setAccessTimer, clearAccessTimer,
}) {
    const _rooms = new Map();   // roomId -> { roomId, owner, ownerPodRoot, container, title, owned }
    const _pendingReg = new Map();   // roomId -> resolve
    let _startedFor = null, _startedRoot = null, _generation = 0, _wasReady = false, _join = null, _sync = null;

    const me = () => getState().webid || null;
    const ownRoot = () => getState().pod_root || null;
    const sessionId = () => `${_generation}\n${me()}\n${ownRoot()}`;
    function context() {
        const session = sessionId(), webId = me(), root = ownRoot();
        return { webId, root, current: () => session === sessionId()
            && !!(getState().ready && getState().started) };
    }
    const joinedRooms = createJoinedRoomStore({
        read: () => bridge.io.podReadJoinedRooms(),
        write: (list) => bridge.io.podWriteJoinedRooms(list),
        readVersion: typeof bridge.io.podReadJoinedRoomsVersion === 'function' ? () => bridge.io.podReadJoinedRoomsVersion() : undefined,
        writeVersion: typeof bridge.io.podWriteJoinedRoomsVersion === 'function' ? (list, version) => bridge.io.podWriteJoinedRoomsVersion(list, version) : undefined,
        getWebId: me,
        getSessionId: sessionId,
        storage,
    });
    const io = createPodRoomIo({
        // A fragment never goes over the wire (and the bridge refuses one), e.g. a WebID.
        fetch: (u, o) => bridge.fetch(String(u).split('#')[0], o),
        isLoggedIn: () => !!getState().ready,
        storageRoot: ownRoot,
        webId: me,
        getSigner,
        fetchPeerSigner: (root) => bridge.io.podFetchPeerSigner(root),
    });

    const ownerMembership = createOwnerMembership({
        read: (roomId) => io.podReadRoomDescriptorVersion(roomId),
        write: (desc, version) => io.podWriteRoomDescriptorVersion(desc, version),
        getOwnerId: me,
        getSessionId: sessionId,
        signDescriptor: (desc) => signDescriptor({ ...desc, name: desc.title,
            code: desc.room_id, creator_webid: desc.owner, created_at: desc.created }),
        apply: (desc, isCurrent, version) => _applyMembers(desc.room_id, desc, isCurrent, version),
    });

    const access = createRoomAccessMonitor({
        fetch: (url, opts) => bridge.fetch(url, opts),
        getSession: () => {
            const state = getState();
            return { ready: !!(state.ready && state.started), webId: state.webid, root: state.pod_root };
        },
        onRemoved: (roomId) => _removed(roomId),
        pollMs: accessPollMs, setTimer: setAccessTimer, clearTimer: clearAccessTimer,
    });

    // Our own messages carry our WebID as maker; the desktop shows them as ours.
    const _asSelf = (m) => (m && m.from_webid && m.from_webid === me() ? { ...m, from_webid: getSelfId() } : m);

    function _ensureSync() {
        if (_sync) return _sync;
        _sync = createRoomSync({
            readDay: async (room, date) => (await io.podReadChatDayAt(room.container, date, room.roomId, room.owner)).map(_asSelf),
            dayUrl: (room, date) => dayFileAt(room.container, date),
            watch: (url, cb) => bridge.watchResource(url, cb),
            onNew: (roomId, msgs, info) => onMessages(roomId, msgs, info),
            onChange: (roomId, msgs) => onMessages(roomId, msgs, { changed: true }),
        });
        return _sync;
    }

    function _add(rec, info = {}) {
        _rooms.set(rec.roomId, rec);
        try { onRoom(rec, info); } catch { /* UI only */ }
        _ensureSync().track({ roomId: rec.roomId, container: rec.container, owner: rec.owner });
        if (!rec.owned) access.track(rec);
    }

    function _drop(roomId) {
        const rec = _rooms.get(roomId);
        if (!rec) return null;
        _rooms.delete(roomId);
        access.untrack(roomId);
        if (_sync) _sync.untrack(roomId);
        if (!rec.owned) { try { send({ cmd: 'pod_room_unregister', container: rec.container }); } catch { /* socket gone */ } }
        return rec;
    }

    // Ask the gateway to let our credentials reach a joined room's container.
    function _register(roomId, ownerWebId) {
        const ctx = context(), requestId = genRoomId();
        return new Promise((resolve) => {
            const finish = r => {
                clearTimeout(timer);
                _pendingReg.delete(requestId);
                resolve(ctx.current() ? r : { ok: false, reason: 'stale' });
            };
            const timer = setTimeout(() => finish({ ok: false, reason: 'timeout' }), timeoutMs);
            _pendingReg.set(requestId, finish);
            try { send({ cmd: 'pod_room_register', req_id: requestId, room_id: roomId, owner_webid: ownerWebId }); }
            catch { finish({ ok: false, reason: 'disconnected' }); }
        });
    }

    async function _updateJoined(change) {
        return joinedRooms.update(change);
    }

    // ── joiner ───────────────────────────────────────────────────────────────

    async function _onApproved(appr, current = () => true) {
        const ctx = context();
        const live = () => ctx.current() && current();
        if (!live()) return false;
        if (!appr || !appr.room_id || !appr.owner_webid) return true;
        const container = vetJoinedRoom(appr.room_id, appr.owner_webid, appr.owner_pod_root, ownRoot());
        if (!container) return true;
        // Only an approval for a join we asked for (the marker in our own pod).
        const pending = await bridge.io.podHasPendingJoin(appr.room_id, appr.owner_webid);
        if (!live()) return false;
        if (pending == null) return false;
        if (!pending) return true;
        const reg = await _register(appr.room_id, appr.owner_webid);
        if (!live()) return false;
        if (!reg.ok) {
            if (reg.reason === 'bearer') { notify('bearer', { roomId: appr.room_id }); return true; }
            return false;   // keep the approval and try again on the next drain
        }
        const rec = { roomId: appr.room_id, owner: appr.owner_webid, ownerPodRoot: appr.owner_pod_root,
            container, title: String(appr.title || appr.room_id).slice(0, 200) };
        if (!(await _updateJoined((list) => withJoinedRecord(list, rec)))) return false;
        if (!live()) return false;
        bridge.io.podDeletePendingJoin(appr.room_id, appr.owner_webid).catch(() => {});
        _add({ ...rec, owned: false }, { joined: true });
        return true;
    }

    async function _onDeclined(msg, current = () => true) {
        const ctx = context();
        if (!ctx.current() || !current()) return false;
        if (!msg || !msg.room_id || !msg.owner_webid) return true;
        const pending = await bridge.io.podHasPendingJoin(msg.room_id, msg.owner_webid);
        if (!ctx.current() || !current()) return false;
        if (pending == null) return false;
        if (!pending) return true;
        bridge.io.podDeletePendingJoin(msg.room_id, msg.owner_webid).catch(() => {});
        notify('declined', { roomId: msg.room_id });
        return true;
    }

    // Persist confirmed removal before clearing the room. A failed own-pod
    // update retains the monitor so a later check can finish the cleanup.
    async function _removed(roomId) {
        const rec = _rooms.get(roomId), account = me(), root = ownRoot();
        if (!rec || rec.owned) return false;
        if (!(await _updateJoined((list) => withoutJoinedRecord(list, roomId)))) return false;
        if (account !== me() || root !== ownRoot() || _rooms.get(roomId) !== rec) return false;
        _drop(roomId);
        try { await onRemoved(roomId); } catch { /* UI only */ }
        return true;
    }

    async function _leave(roomId) {
        const rec = _rooms.get(roomId), account = me(), root = ownRoot();
        if (!rec || rec.owned) return false;
        if (!(await _updateJoined((list) => withoutJoinedRecord(list, roomId)))) return false;
        if (account !== me() || root !== ownRoot() || _rooms.get(roomId) !== rec) return false;
        _drop(roomId);
        return true;
    }

    // ── owner ────────────────────────────────────────────────────────────────

    async function _writeDescriptor(desc) {
        const full = { ...desc, name: desc.title, code: desc.room_id, creator_webid: desc.owner, created_at: desc.created };
        const signed = await signDescriptor(full).catch(() => full);
        return io.podWriteRoomDescriptor(signed);
    }

    // Members (not banned) get Read + Append on the chat; the list beside it is
    // for display. Rewriting both from the descriptor drops anyone left out.
    async function _applyMembers(roomId, desc, isCurrent = () => me() === desc.owner, version) {
        if (!isCurrent()) return false;
        const self = desc.owner;
        const members = desc.members.filter((m) => !m.banned).map((m) => m.webid);
        const container = chatRootUrl(ownRoot(), roomId);
        const granted = await io.podGrantChatParticipants(container, self, members.filter((w) => w !== self), version);
        if (!granted || !isCurrent()) return false;
        return io.podWriteRoomMembers(roomId, members.map((w) => ({ webid: w, role: w === self ? 'admin' : 'member' })));
    }

    async function create(name) {
        const self = me(), root = ownRoot();
        if (!self || !root) return null;
        const roomId = genRoomId();
        const container = chatRootUrl(root, roomId);
        const title = String(name || '').slice(0, 200) || roomId;
        if (!(await io.ensureChatIndexAt(container, title))) return null;
        const desc = buildRoomDescriptor({ roomId, title, owner: self, members: [{ webid: self, role: 'owner' }], longChat: container });
        if (!(await _writeDescriptor(desc))) return null;
        if (!(await io.podSetContainerAcl(`rooms/${roomId}/`, self, []))) return null;
        if (!(await _applyMembers(roomId, desc))) return null;
        const invite = inviteFor(roomId);
        _add({ roomId, owner: self, ownerPodRoot: root, container, title, owned: true }, { created: true, invite });
        return { roomId, title, invite };
    }

    function inviteFor(roomId) {
        return makeInvite(roomId, me(), inviteBase(), { webMode: true });
    }

    async function _approve(webid, roomId, current = () => true, join = _join) {
        if (!current()) return false;
        const result = await ownerMembership.update(roomId, (cur) => {
            const existing = (cur.members || []).find(m => m.webid === webid);
            if (existing?.banned) return null;
            return existing ? cur : withMembers(cur, [...(cur.members || []), { webid, role: 'member' }]);
        });
        if (!current()) return false;
        if (!result.ok || result.descriptor?.owner !== me()) return false;
        if (result.skipped || !result.descriptor.members.some(m => m.webid === webid && !m.banned)) return null;
        return join.sendApproval(webid, roomId, result.descriptor.title || '');
    }

    async function _onJoinRequest(req, current = () => true) {
        const ctx = context(), join = _join;
        const live = () => ctx.current() && current();
        if (!live()) return false;
        if (!req || !req.room_id || typeof req.from_webid !== 'string' || !/^https?:\/\//.test(req.from_webid)) return true;
        const desc = await io.podReadRoomDescriptor(req.room_id);
        if (!live()) return false;
        if (!desc || desc.owner !== me()) return false;   // not readable yet, or not ours: keep it
        if (desc.closed) return join.sendDecline(req.from_webid, req.room_id);
        const existing = (desc.members || []).find((m) => m.webid === req.from_webid);
        if (existing && existing.banned) return true;
        if (existing) {
            return _approve(req.from_webid, req.room_id, live, join);
        }
        const answer = await askApprove(req, desc, live);
        if (!live()) return false;
        if (answer === null) return false;   // unanswered: keep it for next time
        if (answer === 'no') {
            return join.sendDecline(req.from_webid, req.room_id);
        }
        const approved = await _approve(req.from_webid, req.room_id, live, join);
        if (approved === false) return false;
        if (approved !== true) return true;
        notify('approved', { roomId: req.room_id, webid: req.from_webid });
        return true;
    }

    async function kick(roomId, webid) {
        if (webid === me()) return false;
        const result = await ownerMembership.update(roomId, cur => withMembers(cur, cur.members.filter(m => m.webid !== webid)));
        return result.ok && result.descriptor?.owner === me()
            && !result.descriptor.members.some(m => m.webid === webid && !m.banned);
    }

    async function _deleteOwned(roomId) {
        const rec = _rooms.get(roomId);
        if (!rec || !rec.owned) return false;
        rec.closed = true;
        if (!(await _finishDeletion(roomId))) return false;
        _drop(roomId);
        return true;
    }

    async function _finishDeletion(roomId) {
        const root = ownRoot();
        const result = await ownerMembership.remove(roomId, () => root === ownRoot()
            && io.podDeregisterChat(chatRootUrl(root, roomId)));
        return result.ok;
    }

    // ── routing ──────────────────────────────────────────────────────────────

    function _roomOf(cmd) {
        for (const k of _ROOM_KEYS) {
            const id = cmd && cmd[k];
            if (typeof id === 'string' && _rooms.has(id)) return _rooms.get(id);
        }
        return null;
    }

    async function _send(rec, cmd) {
        if (rec.owned) {
            const desc = await io.podReadRoomDescriptor(rec.roomId);
            if (!desc || desc.closed) {
                handleEvent({ type: 'error', cmd: 'send_room', room_id: rec.roomId,
                    message_id: cmd.message_id, message: 'pod_operation_failed' });
                return;
            }
        }
        const msg = {
            content: String(cmd.content || ''),
            from_webid: me(),
            from_display_name: getDisplayName() || '',
            timestamp: new Date().toISOString(),
            reply_to_id: cmd.reply_to_id || null,
            room_name: rec.title,
        };
        if (_sync) _sync.markSeen(rec.roomId, cmd.message_id);
        const r = await io.podWriteChatMessageAtResult(rec.container, cmd.message_id, msg);
        if (r.ok) {
            handleEvent({
                type: 'message', message_id: cmd.message_id, thread_id: rec.roomId, source: 'local_room',
                from_webid: getSelfId(), from_display_name: msg.from_display_name, content: msg.content,
                timestamp: msg.timestamp, pod_ts: msg.timestamp, reply_to_id: msg.reply_to_id, local: true,
            });
        } else if (r.status === 403 && !r.policy && !rec.owned) {
            await access.check(rec.roomId, { denied: true });
        }
    }

    async function _history(rec, cmd) {
        let messages = [];
        try { messages = (await io.podReadChatRecentAt(rec.container, 7, rec.roomId, rec.owner)).map(_asSelf); } catch { messages = []; }
        messages = messages.filter((m) => m && m.message_id && !m.deleted)
            .sort((a, b) => String(a.timestamp || '').localeCompare(String(b.timestamp || '')));
        handleEvent({ type: 'local_history', thread_id: cmd.thread_id, messages: messages.slice(-(cmd.limit || 100)), last_read_ts: 0 });
    }

    async function _members(rec) {
        let list = null;
        if (rec.owned) {
            const d = await io.podReadRoomDescriptor(rec.roomId);
            if (d) list = d.members.filter((m) => !m.banned).map((m) => ({ webid: m.webid, role: m.role }));
        } else {
            list = await io.podReadRoomMembersAt(rec.container);
        }
        if (!list) return;
        const members = list.slice(0, 200).map((m) => ({
            webid: m.webid === me() ? getSelfId() : m.webid,
            role: m.role, display_name: m.webid === me() ? (getDisplayName() || '') : '',
        }));
        handleEvent({ type: 'room_members', room_id: rec.roomId, members, from_pod: true });
    }

    // Route an app command. Returns true when it was a pod room's (the caller
    // must not also send it to the gateway).
    function route(cmd) {
        if (!cmd || typeof cmd.cmd !== 'string' || cmd.cmd.startsWith('pod_')) return false;   // the bridge's own
        if (cmd.cmd === 'join_web_room') {
            const inv = parseInvite(cmd.code);
            if (!inv) handleEvent({ type: 'join_invalid_invite' });
            else if (!getState().ready || !_join) handleEvent({ type: 'join_invalid_invite', reason: 'no_pod' });
            else if (inv.ownerWebId === me()) handleEvent({ type: 'join_invalid_invite', reason: 'own_room' });
            else {
                _join.requestJoin(inv.roomId, inv.ownerWebId).then((ok) => {
                    if (ok === false) handleEvent({ type: 'join_invalid_invite', reason: 'unreachable' });
                    else handleEvent({ type: 'join_request_sent', room_id: inv.roomId, owner_webid: inv.ownerWebId });
                }).catch(() => handleEvent({ type: 'join_invalid_invite', reason: 'unreachable' }));
            }
            return true;
        }
        if (cmd.cmd === 'chat_room_create' && cmd.pod) {
            create(cmd.name).then((r) => {
                if (r) handleEvent({ type: 'room_created', room_id: r.roomId, name: r.title, code: r.roomId, invite_url: r.invite, pod_room: true });
                else notify('create_failed', {});
            }).catch(() => notify('create_failed', {}));
            return true;
        }
        const rec = _roomOf(cmd);
        if (!rec) return false;
        switch (cmd.cmd) {
            case 'send_room': _send(rec, cmd).catch(() => {}); break;
            case 'get_local_history': _history(rec, cmd).catch(() => {}); break;
            case 'get_room_members': _members(rec).catch(() => {}); break;
            case 'leave_local_room':
                if (!rec.owned) {
                    _leave(rec.roomId).then((ok) => {
                        if (ok) handleEvent({ type: 'left_room', room_id: rec.roomId });
                        else handleEvent({ type: 'error', cmd: 'leave_local_room', room_id: rec.roomId, message: 'pod_operation_failed' });
                    }).catch(() => handleEvent({ type: 'error', cmd: 'leave_local_room', room_id: rec.roomId, message: 'pod_operation_failed' }));
                }
                break;
            case 'delete_room':
                _deleteOwned(rec.roomId).then((ok) => {
                    handleEvent(ok ? { type: 'room_deleted', room_id: rec.roomId }
                        : { type: 'error', cmd: 'delete_room', room_id: rec.roomId, message: 'pod_operation_failed' });
                }).catch(() => handleEvent({ type: 'error', cmd: 'delete_room', room_id: rec.roomId, message: 'pod_operation_failed' }));
                break;
            case 'kick_member':
                if (rec.owned) {
                    kick(rec.roomId, cmd.webid).then((ok) => {
                        if (ok) handleEvent({ type: 'member_kicked', room_id: rec.roomId, webid: cmd.webid });
                    }).catch(() => {});
                }
                break;
            default:
                break;   // gateway-only room features have no pod protocol yet
        }
        return true;
    }

    function handleEvent_(ev) {
        if (!ev || ev.type !== 'pod_room_registered') return false;
        const done = _pendingReg.get(ev.req_id);
        if (done) done({ ok: !!ev.ok, reason: ev.reason || '' });
        return true;
    }

    // ── lifecycle ────────────────────────────────────────────────────────────

    async function _restore() {
        const ctx = context(), self = ctx.webId;
        if (!ctx.current()) return;
        const joined = await joinedRooms.list();
        if (!ctx.current()) return;
        for (const r of joined) {
            const container = vetJoinedRoom(r.roomId, r.owner, r.ownerPodRoot, ctx.root);
            if (!container || container !== r.container || _rooms.has(r.roomId)) continue;
            const reg = await _register(r.roomId, r.owner);
            if (!ctx.current()) return;
            if (!reg.ok) { if (reg.reason === 'bearer') notify('bearer', { roomId: r.roomId }); continue; }
            _add({ ...r, container, owned: false }, {});
        }
        const owned = await io.podListOwnedRoomDescriptors(self);
        if (!ctx.current()) return;
        for (const d of owned) {
            if (_rooms.has(d.room_id)) continue;
            if (d.closed && d.deletion_complete) continue;
            const deleted = d.closed && await _finishDeletion(d.room_id);
            if (!ctx.current()) return;
            if (deleted) continue;
            _add({ roomId: d.room_id, owner: self, ownerPodRoot: ctx.root, container: chatRootUrl(ctx.root, d.room_id),
                title: d.title || d.room_id, owned: true, ...(d.closed ? { closed: true } : {}) },
            { invite: d.closed ? '' : inviteFor(d.room_id) });
            if (d.closed) handleEvent({ type: 'error', cmd: 'delete_room', room_id: d.room_id, message: 'pod_operation_failed' });
        }
    }

    // The gateway forgot our registrations (it restarted, or the socket did):
    // register the joined rooms again, then re-arm their watches.
    async function _reRegister() {
        const ctx = context();
        for (const rec of _rooms.values()) {
            if (!rec.owned) {
                const reg = await _register(rec.roomId, rec.owner);
                if (!ctx.current()) return;
                if (reg.ok) access.track(rec);
            }
            _ensureSync().track(rec);
        }
        if (!ctx.current()) return;
        bridge.resubscribe();
        access.checkAll().catch(() => {});
        if (_join) _join.drainOnce().catch(() => {});
    }

    // Called with every cross-mode state change. Starts once the DM side has
    // published our identity (state.started), so the join box is ours to read.
    function onState(st) {
        const ready = !!(st && st.ready && st.started);
        const changed = ready && (_startedFor !== st.webid || _startedRoot !== st.pod_root);
        if (!ready || changed || !_wasReady) {
            _generation++;
            access.stop();
            if (_join) _join.stop();
            if (_sync) _sync.stop();
            for (const finish of [..._pendingReg.values()]) finish({ ok: false, reason: 'stale' });
        }
        if (changed) {
            _startedFor = st.webid;
            _startedRoot = st.pod_root;
            const previous = [..._rooms.values()];
            for (const id of [..._rooms.keys()]) _drop(id);
            try { onReset(previous); } catch { /* UI only */ }
            _join = createWebJoin({
                pod: bridge.io,
                notify: { watchResource: (url, cb) => bridge.watchResource(url, cb) },
                getSelfWebId: me,
                getSessionId: sessionId,
                storage, onStatusChange: onJoinStatus,
                getDisplayName,
                getSelfPodRoot: ownRoot,
                peerPodRoot: (w) => peerPodRootFromWebId(w),
                onJoinRequest: (req) => _onJoinRequest(req),
                onApproved: (appr) => _onApproved(appr),
                onDeclined: (msg) => _onDeclined(msg),
            });
            _join.start().catch(() => {});
            _restore().catch(() => {});
        } else if (ready && !_wasReady) {
            _join?.start().catch(() => {});
            const ctx = context();
            _reRegister().then(() => { if (ctx.current()) return _restore(); }).catch(() => {});
        }
        _wasReady = ready;
    }

    async function changeMessage(roomId, messageId, timestamp, change) {
        const ctx = context(), rec = _rooms.get(roomId);
        if (!rec || !ctx.current()) return { ok: false, reason: 'account_changed' };
        const current = () => ctx.current() && _rooms.get(roomId) === rec;
        const result = await io.podChangeMessageAt(rec.container, messageId, timestamp, change, rec.owner, current);
        return current() ? { ...result, ...(result.message ? { message: _asSelf(result.message) } : {}) } : { ok: false, reason: 'account_changed' };
    }

    return {
        changeMessage,
        route, handleEvent: handleEvent_, onState,
        isPodRoom: (id) => _rooms.has(id),
        list: () => [..._rooms.values()],
        room: (id) => _rooms.get(id) || null,
        inviteFor, create, kick,
        drainJoins: () => (_join ? _join.drainOnce() : Promise.resolve()),
        refresh: (id) => (_sync ? _sync.refresh(id) : Promise.resolve()),
        checkAccess: (id, opts) => id ? access.check(id, opts) : access.checkAll(),
        io,
    };
}
