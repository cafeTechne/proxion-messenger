// webjoin.js — gateway-free room-join handshake (R106).
//
// A room lives on its owner's pod. Without a gateway to grant access, joining is
// a request/approve handshake over a pod drop box (proxion/join-inbox/):
//   1. The owner shares an invite link (room id + owner WebID).
//   2. The joiner drops a `join_request` into the owner's join inbox.
//   3. The owner (when online) is prompted; on approve it grants the joiner ACL
//      on the room container and drops a `join_approved` back to the joiner.
//   4. The joiner reads the room from the owner's pod and adds it locally.
//   On decline the owner drops a `join_declined` instead, so the joiner stops
//   waiting.
//
// This module owns only the drop/receive mechanics and the invite encoding; the
// app supplies the owner-side approve action and the joiner-side registration
// via callbacks. Dependencies are injected so it is unit-testable.

import { createRoomApprovalStore } from './room-approvals.js';

const SEP = '~';

/** Encode an invite the owner can share: an ?join= link plus the raw token.
 *  The link is built from the app's origin + path, so a query or #fragment on the
 *  owner's current URL never ends up inside the token. `webMode` adds mode=web,
 *  so a copy of the app served without the web-build meta tag still opens the
 *  link in the browser build. */
export function makeInvite(roomId, ownerWebId, appUrl = '', { webMode = false } = {}) {
    const token = encodeURIComponent(roomId) + SEP + encodeURIComponent(ownerWebId);
    if (!appUrl) return token;
    let base;
    try {
        const u = new URL(appUrl);
        base = u.origin + u.pathname;
    } catch {
        base = String(appUrl).split(/[?#]/)[0];
    }
    return base + '?join=' + token + (webMode ? '&mode=web' : '');
}

/** Parse an invite (a full ?join= URL or a raw token) to { roomId, ownerWebId }. */
export function parseInvite(str) {
    if (!str) return null;
    let token = String(str).trim();
    if (/^https?:\/\//.test(token)) {
        try { token = new URL(token).searchParams.get('join') || ''; } catch { return null; }
    }
    const i = token.indexOf(SEP);
    if (i < 0) return null;
    let roomId, ownerWebId;
    try {
        roomId = decodeURIComponent(token.slice(0, i));
        ownerWebId = decodeURIComponent(token.slice(i + 1));
    } catch { return null; }   // malformed percent-encoding
    // Accept http(s) WebIDs: production pods are https, but a local/dev pod may be
    // http, and the owner's WebID is where the join request is delivered.
    if (!roomId || !/^https?:\/\//.test(ownerWebId)) return null;
    return { roomId, ownerWebId };
}

export function createWebJoin({
    pod, notify, getSelfWebId, getDisplayName, getSelfPodRoot, peerPodRoot,
    onJoinRequest, onApproved, onDeclined,
    getSessionId = getSelfWebId,
    storage = globalThis.localStorage, onStatusChange = () => {},
    pollMs = 15000, setTimer = (fn, ms) => setInterval(fn, ms), clearTimer = (h) => clearInterval(h),
}) {
    let _unsub = null;
    let _retryTimer = null;
    let _generation = 0, _active = true;
    const account = () => ({ webId: getSelfWebId(), root: getSelfPodRoot?.(), session: getSessionId() });
    let _account = account();
    const statusStore = () => createRoomApprovalStore({ storage, webId: _account.webId, root: _account.root });
    let _status = statusStore(), _probeOffset = 0;
    function publish() { try { onStatusChange(_status.list(), () => drainOnce()); } catch { /* status UI must not block delivery */ } }
    function context() {
        const generation = _generation, identity = _account;
        return () => _active && generation === _generation && identity === _account
            && identity.webId === getSelfWebId() && identity.root === getSelfPodRoot?.()
            && identity.session === getSessionId();
    }
    // Drops being handled right now, and drops already consumed. A notification
    // drain can overlap the start() drain (or another notification), and both
    // list the same request: without these the owner would be prompted twice
    // for one request, or prompted again after answering, before the delete lands.
    const _inFlight = new Map();
    const _probing = new Map();
    const _done = new Set();
    let _inboxWork = null;

    async function ensureListening() {
        if (_unsub || _inboxWork) return _inboxWork;
        const current = context();
        const work = (async () => {
            let inbox;
            try { inbox = await pod.podEnsureJoinInbox(); } catch { return null; }
            if (!current()) return null;
            if (inbox && notify?.watchResource) {
                try { _unsub = notify.watchResource(inbox, () => { if (current()) drainOnce().catch(() => {}); }); } catch { /* polling remains active */ }
            }
            return inbox;
        })();
        _inboxWork = work;
        try { return await work; } finally { if (_inboxWork === work) _inboxWork = null; }
    }

    // Joiner: ask an owner to let us into a room.
    async function requestJoin(roomId, ownerWebId) {
        const current = context(), webId = getSelfWebId(), name = getDisplayName?.() || '';
        if (!current()) return false;
        const root = peerPodRoot(ownerWebId);
        if (!root) return false;
        // Persist authorization BEFORE publishing the request. The owner may
        // answer immediately (including auto-approval of an existing member),
        // so writing afterwards lets the approval race ahead of its marker.
        // A failed write must leave the request unsent: otherwise every approval
        // would be rejected as unsolicited while the UI says we are waiting.
        if (pod.podWritePendingJoin) {
            try {
                if (await pod.podWritePendingJoin(roomId, ownerWebId) !== true) return false;
            } catch { return false; }
        }
        if (!current()) return false;
        return pod.podDropJoin(root, {
            kind: 'join_request',
            room_id: roomId,
            from_webid: webId,
            from_display_name: name,
        });
    }

    // Owner: tell a joiner they are in (after granting them ACL). Carries where
    // the room lives so the joiner can read it.
    async function sendApproval(toWebId, roomId, title) {
        const current = context();
        if (!current()) return false;
        const root = peerPodRoot(toWebId);
        if (!root) return false;
        return pod.podDropJoin(root, {
            kind: 'join_approved',
            room_id: roomId,
            owner_webid: getSelfWebId(),
            owner_pod_root: getSelfPodRoot ? getSelfPodRoot() : null,
            title: title || '',
        });
    }

    // Owner: tell a joiner the request was declined, so they stop waiting.
    async function sendDecline(toWebId, roomId) {
        const current = context();
        if (!current()) return false;
        const root = peerPodRoot(toWebId);
        if (!root) return false;
        return pod.podDropJoin(root, {
            kind: 'join_declined',
            room_id: roomId,
            owner_webid: getSelfWebId(),
        });
    }

    async function _handle(url, msg, current) {
        // Default: remove a handled or unrecognized message. A handler that
        // returns exactly `false` (e.g. the owner's room descriptor is not
        // readable yet, or the owner closed the tab without answering) keeps the
        // message so a later drain can retry, rather than silently consuming a
        // request the owner never got to act on.
        let consumed = _status.get(url, msg)?.state === 'cleanup';
        if (!consumed && msg?.kind === 'join_request') {
            _status.put(url, msg, 'pending');
            publish();
        }
        try {
            if (!consumed) {
                consumed = true;
                if (msg && msg.kind === 'join_request' && onJoinRequest) consumed = (await onJoinRequest(msg, current)) !== false;
                else if (msg && msg.kind === 'join_approved' && onApproved) consumed = (await onApproved(msg, current)) !== false;
                else if (msg && msg.kind === 'join_declined' && onDeclined) consumed = (await onDeclined(msg, current)) !== false;
            }
        } catch (err) {
            console.warn('[webjoin] handling failed:', err);
            consumed = false;
        }
        if (!current()) return;
        _status.put(url, msg, consumed ? 'cleanup' : 'retry');
        if (!consumed) publish();
        if (consumed) {
            let deleted = false;
            try { deleted = await pod.podDeleteJoin(url) === true; } catch { /* retry cleanup without replaying the decision */ }
            if (!current()) return;
            if (deleted) {
                _done.add(url);
                if (_done.size > 5000) _done.delete(_done.values().next().value);
                _status.remove(url);
            }
            publish();
        }
    }

    // Handles every drop concurrently, so a join prompt waiting on the owner does
    // not hold up an approval behind it. The app queues its prompts one at a time.
    async function drainOnce() {
        const current = context();
        if (!current()) return;
        const joins = await pod.podReadJoins();
        if (!current()) return;
        const work = [];
        for (const { url, msg } of joins) {
            if (_inFlight.has(url) || _done.has(url)) continue;
            _inFlight.set(url, current);
            work.push(_handle(url, msg, current).finally(() => {
                if (_inFlight.get(url) === current) _inFlight.delete(url);
            }));
        }
        await Promise.all(work);
        if (!current() || !pod.podJoinExists) return;
        // Confirm records finished by another device without holding up new
        // requests. Failed/empty listings alone never clear a retry notice.
        const urls = new Set(joins.map(row => row.url));
        const missing = _status.list().filter(row => !urls.has(row.url));
        const probes = [];
        for (let i = 0; i < Math.min(20, missing.length); i++) {
            const row = missing[(_probeOffset + i) % missing.length];
            if (_probing.has(row.url)) continue;
            _probing.set(row.url, current);
            probes.push((async () => {
                try {
                    const exists = await pod.podJoinExists(row.url);
                    if (current() && exists === false) { _status.remove(row.url); publish(); }
                } catch { /* retain status on network/session failure */ }
                finally { if (_probing.get(row.url) === current) _probing.delete(row.url); }
            })());
        }
        _probeOffset += 20;
        await Promise.all(probes);
    }

    async function start() {
        stop();
        _active = true;
        _account = account();
        _status = statusStore();
        publish();
        const current = context();
        // Arm recovery before inbox creation: an offline/failed first request
        // must not leave this account deaf until a full page reload.
        _retryTimer = setTimer(() => { if (current()) {
            ensureListening().catch(() => {});
            drainOnce().catch(() => {});
        } }, pollMs);
        const inbox = await ensureListening();
        if (!current()) return null;
        await drainOnce();
        return inbox;
    }

    function stop() {
        _active = false;
        _generation++;
        _inFlight.clear();
        _probing.clear();
        _done.clear();
        _inboxWork = null;
        try { onStatusChange([], null); } catch { /* optional status UI */ }
        if (_retryTimer !== null) { clearTimer(_retryTimer); _retryTimer = null; }
        if (_unsub) { try { _unsub(); } catch { /* ignore */ } _unsub = null; }
    }

    function resume() {
        const current = context();
        if (!current()) return start();
        ensureListening().catch(() => {});
        return drainOnce();
    }
    return { requestJoin, sendApproval, sendDecline, drainOnce, start, stop, resume };
}
