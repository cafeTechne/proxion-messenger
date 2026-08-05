// identity.js — one place that answers questions about identity relationships.
//
// Proxion has three kinds of identity (account, gateway, device) that are easy to
// confuse, and confusing them has caused real bugs. See docs/IDENTITY.md for the full
// contract. This module centralizes the two questions code keeps asking, so call sites
// stop reconciling identities by hand:
//
//   selfDeviceDid()   — the key THIS client signs with (its browser/device did).
//   selfAccountDid()  — who others know us as (our account did; the device did when a
//                       single device is also the account).
//   contactForCall(view, event) — reduce an identity seen on a call (an event's
//                       caller_webid/from_webid, or the open thread's peer) to the
//                       account did of a KNOWN contact, or '' when we don't recognize it.
//
// Pure: all state is read through injected getters, so this is trivially testable and
// holds no identity of its own.

export function createIdentityResolver({
    getClientDid = () => null,
    getAccountDid = () => null,
    getPeerDidToCertId = () => ({}),
    getLocalDmPeers = () => ({}),
    getPeerDeviceKeys = () => ({}),
    isCallCapablePeer = () => false,
} = {}) {
    const isDidKey = (s) => typeof s === 'string' && s.startsWith('did:key:');

    // The key we sign with (the DTLS fingerprint, the auth challenge). Always the
    // device's own key, even on a device linked to an account.
    function selfDeviceDid() {
        return getClientDid() || null;
    }

    // The identity others know us by. On a linked device this is the account did; on a
    // single device the device did IS the account, so fall back to it.
    function selfAccountDid() {
        return getAccountDid() || getClientDid() || null;
    }

    // Reduce an identity on a call to the contact (account did) it belongs to, or ''.
    // Priority: an explicit account identity carried on the event (caller_webid, then
    // from_webid) that we have a relationship with; otherwise the open thread's peer.
    // Never returns a gateway-only or unrecognized identity, so a caller can safely
    // treat a non-empty result as "the contact we expect."
    function contactForCall(view, event) {
        const map = getPeerDidToCertId() || {};
        const onWire = event && (event.caller_webid || event.from_webid);
        if (isDidKey(onWire) && map[onWire]) return onWire;
        if (view) {
            if (isDidKey(view.peerWebid)) return view.peerWebid;
            if (view.id) {
                const found = Object.keys(map).find((d) => map[d] === view.id);
                if (found) return found;
            }
        }
        return '';
    }

    // Whether a contact is known to bind their calls (their relationship advertises it,
    // or we have accepted a bound call from them before). When true, an unbindable call
    // from them is treated as a downgrade rather than an old client. See docs/CALLS.md
    // and PLAN_ROUND_86.
    function peerBindsCalls(contactDid) {
        return !!(contactDid && isCallCapablePeer(contactDid));
    }

    // Reduce a sidebar thread id to the identity the gateway keys a mute by. The gateway
    // mutes by the peer's webid (DMs) or the room_id (rooms) so it can honor mute for
    // OFFLINE push, where the client's mutedThreads set is invisible and per-side cert_ids
    // differ. Local DM peer's webid wins; else the peer did whose cert_id equals the
    // thread id; else the thread id unchanged (a room_id). See PLAN_ROUND_87.
    function serverMuteKey(threadId) {
        if (!threadId) return threadId;
        const dm = (getLocalDmPeers() || {})[threadId];
        if (dm && dm.peer_webid) return dm.peer_webid;
        const map = getPeerDidToCertId() || {};
        for (const peerDid of Object.keys(map)) {
            if (map[peerDid] === threadId) return peerDid;
        }
        return threadId;
    }

    // Multi-device fanout (R89): which devices a DM is sealed to. The resolver only
    // decides WHICH devices; the send path still owns the sealing and session handling.
    //   devicesForPeer  — the peer account's device entries (or []).
    //   ownOtherDevices — our account's device entries EXCLUDING this device, the self-
    //                     sync set. The "never seal to yourself" exclusion lives here so
    //                     it is not re-derived per call site (a device we seal to and then
    //                     discard poisons its ratchet session). See PLAN_ROUND_89.
    function devicesForPeer(accountDid) {
        return (getPeerDeviceKeys() || {})[accountDid] || [];
    }
    function ownOtherDevices() {
        const myDevice = selfDeviceDid();
        return ((getPeerDeviceKeys() || {})[selfAccountDid()] || [])
            .filter((d) => d.device_id && d.device_id !== myDevice);
    }

    return {
        selfDeviceDid, selfAccountDid, contactForCall, peerBindsCalls, serverMuteKey,
        devicesForPeer, ownOtherDevices,
    };
}
