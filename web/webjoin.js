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
}) {
    let _unsub = null;
    // Drops being handled right now, and drops already consumed. A notification
    // drain can overlap the start() drain (or another notification), and both
    // list the same request: without these the owner would be prompted twice
    // for one request, or prompted again after answering, before the delete lands.
    const _inFlight = new Set();
    const _done = new Set();

    // Joiner: ask an owner to let us into a room.
    async function requestJoin(roomId, ownerWebId) {
        const root = peerPodRoot(ownerWebId);
        if (!root) return false;
        const ok = await pod.podDropJoin(root, {
            kind: 'join_request',
            room_id: roomId,
            from_webid: getSelfWebId(),
            from_display_name: getDisplayName ? getDisplayName() : '',
        });
        // Record a pending marker in our OWN pod so a later `join_approved` can be
        // checked against a request we actually made. Without this, anyone who
        // knows our WebID could drop a forged approval into the public-Append join
        // inbox and force a room in. Best-effort: a failed marker just means the
        // approval will be treated as unsolicited (safe default).
        if (ok !== false && pod.podWritePendingJoin) {
            try { await pod.podWritePendingJoin(roomId, ownerWebId); } catch { /* best-effort */ }
        }
        return ok;
    }

    // Owner: tell a joiner they are in (after granting them ACL). Carries where
    // the room lives so the joiner can read it.
    async function sendApproval(toWebId, roomId, title) {
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
        const root = peerPodRoot(toWebId);
        if (!root) return false;
        return pod.podDropJoin(root, {
            kind: 'join_declined',
            room_id: roomId,
            owner_webid: getSelfWebId(),
        });
    }

    async function _handle(url, msg) {
        // Default: remove a handled or unrecognized message. A handler that
        // returns exactly `false` (e.g. the owner's room descriptor is not
        // readable yet, or the owner closed the tab without answering) keeps the
        // message so a later drain can retry, rather than silently consuming a
        // request the owner never got to act on.
        let consumed = true;
        try {
            if (msg && msg.kind === 'join_request' && onJoinRequest) consumed = (await onJoinRequest(msg)) !== false;
            else if (msg && msg.kind === 'join_approved' && onApproved) consumed = (await onApproved(msg)) !== false;
            else if (msg && msg.kind === 'join_declined' && onDeclined) consumed = (await onDeclined(msg)) !== false;
        } catch (err) {
            console.warn('[webjoin] handling failed:', err);
            consumed = false;
        }
        if (consumed) {
            _done.add(url);
            await pod.podDeleteJoin(url);
        }
    }

    // Handles every drop concurrently, so a join prompt waiting on the owner does
    // not hold up an approval behind it. The app queues its prompts one at a time.
    async function drainOnce() {
        const joins = await pod.podReadJoins();
        const work = [];
        for (const { url, msg } of joins) {
            if (_inFlight.has(url) || _done.has(url)) continue;
            _inFlight.add(url);
            work.push(_handle(url, msg).finally(() => _inFlight.delete(url)));
        }
        await Promise.all(work);
    }

    async function start() {
        const inbox = await pod.podEnsureJoinInbox();
        await drainOnce();
        if (inbox && notify && notify.watchResource) {
            _unsub = notify.watchResource(inbox, () => { drainOnce().catch(() => {}); });
        }
        return inbox;
    }

    function stop() {
        if (_unsub) { try { _unsub(); } catch { /* ignore */ } _unsub = null; }
    }

    return { requestJoin, sendApproval, sendDecline, drainOnce, start, stop };
}
