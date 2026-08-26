// webjoin.js — gateway-free room-join handshake (R106).
//
// A room lives on its owner's pod. Without a gateway to grant access, joining is
// a request/approve handshake over a pod drop box (proxion/join-inbox/):
//   1. The owner shares an invite link (room id + owner WebID).
//   2. The joiner drops a `join_request` into the owner's join inbox.
//   3. The owner (when online) is prompted; on approve it grants the joiner ACL
//      on the room container and drops a `join_approved` back to the joiner.
//   4. The joiner reads the room from the owner's pod and adds it locally.
//
// This module owns only the drop/receive mechanics and the invite encoding; the
// app supplies the owner-side approve action and the joiner-side registration
// via callbacks. Dependencies are injected so it is unit-testable.

const SEP = '~';

/** Encode an invite the owner can share: an ?join= link plus the raw token. */
export function makeInvite(roomId, ownerWebId, appUrl = '') {
    const token = encodeURIComponent(roomId) + SEP + encodeURIComponent(ownerWebId);
    return appUrl ? (appUrl.split('?')[0] + '?join=' + token) : token;
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
    const roomId = decodeURIComponent(token.slice(0, i));
    const ownerWebId = decodeURIComponent(token.slice(i + 1));
    // Accept http(s) WebIDs: production pods are https, but a local/dev pod may be
    // http, and the owner's WebID is where the join request is delivered.
    if (!roomId || !/^https?:\/\//.test(ownerWebId)) return null;
    return { roomId, ownerWebId };
}

export function createWebJoin({
    pod, notify, getSelfWebId, getDisplayName, getSelfPodRoot, peerPodRoot,
    onJoinRequest, onApproved,
}) {
    let _unsub = null;

    // Joiner: ask an owner to let us into a room.
    async function requestJoin(roomId, ownerWebId) {
        const root = peerPodRoot(ownerWebId);
        if (!root) return false;
        return pod.podDropJoin(root, {
            kind: 'join_request',
            room_id: roomId,
            from_webid: getSelfWebId(),
            from_display_name: getDisplayName ? getDisplayName() : '',
        });
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

    async function drainOnce() {
        const joins = await pod.podReadJoins();
        for (const { url, msg } of joins) {
            // Default: remove a handled or unrecognized message. A handler that
            // returns exactly `false` (e.g. the owner's room descriptor is not
            // readable yet) keeps the message so a later drain can retry, rather
            // than silently consuming a request the owner never got to act on.
            let consumed = true;
            try {
                if (msg && msg.kind === 'join_request' && onJoinRequest) consumed = (await onJoinRequest(msg)) !== false;
                else if (msg && msg.kind === 'join_approved' && onApproved) consumed = (await onApproved(msg)) !== false;
            } catch (err) {
                console.warn('[webjoin] handling failed:', err);
                consumed = false;
            }
            if (consumed) await pod.podDeleteJoin(url);
        }
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

    return { requestJoin, sendApproval, drainOnce, start, stop };
}
