// webcalls.js — gateway-free 1:1 call signaling (R105).
//
// WebRTC media is already peer-to-peer; only the signaling (offer/answer/ICE/
// hangup) needs a path. Without a gateway to relay it, each signaling message is
// dropped into the callee's pod call-inbox (pod.js podDropSignal) and delivered
// by watching our own call-inbox (notify.js). The pod is untrusted signaling
// only: callsec.js signs the DTLS fingerprint, so a tampered offer/answer is
// refused exactly as it would be against an untrusted gateway. Media never
// touches the pod, and STUN handles NAT for most networks (TURN, when a
// restrictive NAT needs it, is a separate relay the operator supplies).
//
// Dependencies are injected so the engine is unit-testable without a browser.

import { isDmPeerWebId } from './webdm.js';

// The outbound commands voice.js emits that this engine carries.
export const SIGNAL_CMDS = new Set(['voice_invite', 'voice_answer', 'ice_candidate', 'voice_hangup']);

// The call inbox is public-Append: anyone who knows our WebID can POST arbitrary
// JSON into it. It legitimately carries only the signal types we drop into a
// peer's inbox (sendSignal maps cmd -> type), so the receive-side allowlist is
// that same set. Dispatch only these voice types; anything else (a forged
// contact_revoked, message, block, ...) would otherwise run through the full
// client dispatch.
const RECV_TYPES = SIGNAL_CMDS;

export function createWebCalls({ pod, notify, handleEvent, getSelfWebId, getDisplayName, peerPodRoot, isBlocked, isAllowedCaller }) {
    let _unsub = null;
    let _inbox = null;
    let _active = false;
    let _draining = false;
    let _drainAgain = false;

    // Turn an outbound voice.js command into a signal and drop it to the callee.
    // The gateway would normally stamp the sender; here we add from/caller_webid
    // so the receiver's event handlers can route it.
    async function sendSignal(cmd) {
        if (!cmd || !cmd.target_webid) return false;
        const { cmd: type, target_webid, ...rest } = cmd;
        const signal = {
            type,
            from_webid: getSelfWebId(),
            caller_webid: getSelfWebId(),
            display_name: getDisplayName ? getDisplayName() : '',
            ...rest,
        };
        return pod.podDropSignal(peerPodRoot(target_webid), signal);
    }

    // Read pending signals, hand each to the app's event dispatch, and delete it
    // (signals are transient; a consumed one must not be replayed).
    async function drainOnce() {
        // Coalesce concurrent drains: two pod-change notifications close together
        // would otherwise both read the same not-yet-deleted signals and dispatch
        // each one twice (duplicate ringing / duplicate offer). Run one at a time,
        // and re-run once if a notification arrived mid-drain.
        if (_draining) { _drainAgain = true; return; }
        _draining = true;
        try {
            do {
                _drainAgain = false;
                const sigs = await pod.podReadSignals();
                for (const { url, signal } of sigs) {
                    try {
                        // A blocked caller never rings: drop their signals unread.
                        const blocked = !!(signal && isBlocked && (isBlocked(signal.from_webid) || isBlocked(signal.caller_webid)));
                        // Where only contacts may ring (the desktop), a stranger's
                        // signals are dropped too; they can still send a message request.
                        const allowed = !isAllowedCaller || !!(signal && isAllowedCaller(signal.from_webid)
                            && (!signal.caller_webid || signal.caller_webid === signal.from_webid));
                        if (signal && !blocked && allowed && RECV_TYPES.has(signal.type)) handleEvent(signal);
                    } catch (err) {
                        console.warn('[webcalls] signal handling failed:', err);
                    }
                    await pod.podDeleteSignal(url);
                }
            } while (_drainAgain);
        } finally {
            _draining = false;
        }
    }

    async function start() {
        const inbox = await pod.podEnsureCallInbox();
        _inbox = inbox;
        await drainOnce();
        if (inbox && notify && notify.watchResource) {
            _unsub = notify.watchResource(inbox, () => { drainOnce().catch(() => {}); }, { fast: _active });
        }
        return inbox;
    }

    // Ringing or in a call: watch the inbox closely, so an answer, ICE and a
    // hangup arrive within a second where the watch is a poll (the gateway's pod
    // bridge). Back to the normal pace once the call is over.
    function setActive(active) {
        active = !!active;
        if (active === _active) return;
        _active = active;
        if (_inbox && notify && notify.setFast) notify.setFast(_inbox, active);
    }

    function stop() {
        if (_unsub) { try { _unsub(); } catch { /* ignore */ } _unsub = null; }
    }

    return { sendSignal, drainOnce, start, stop, setActive };
}

// The WebID a call is with: the one the signal names, else the open thread's.
export function callPeerWebId(view, event) {
    const onWire = event && (event.caller_webid || event.from_webid);
    if (isDmPeerWebId(onWire)) return onWire;
    if (view) {
        if (isDmPeerWebId(view.peerWebid)) return view.peerWebid;
        if (view.type === 'local_dm' && isDmPeerWebId(view.id)) return view.id;
    }
    return null;
}

// Whose key must sign a WebID peer's DTLS fingerprint (callsec.js) for the call
// to read Verified: the signer their pod's proxion/identity/signer.json names,
// read fresh for each call and cached for when the pod is unreachable. With no
// signer known the result is a placeholder no key matches, so the call can
// connect as Unverified but never as Verified: an unknown expected identity
// would otherwise let any self-consistent signature pass (classifyPeerSdp).
export const UNRESOLVED_SIGNER = 'unresolved-signer:';

export function createCallSigners({ fetchSigner }) {
    const _cache = new Map();   // webid -> signer did

    async function _fetch(webid) {
        try {
            const s = await fetchSigner(webid);
            const did = s && typeof s.signer === 'string' && s.signer.startsWith('did:key:') ? s.signer : null;
            if (did) _cache.set(webid, did);
            return did;
        } catch {
            return null;
        }
    }

    async function resolve(webid) {
        if (!isDmPeerWebId(webid)) return null;
        return (await _fetch(webid)) || _cache.get(webid) || null;
    }

    // The expected signer for a call, or '' when the peer is not a WebID.
    async function expectedFor(view, event) {
        const webid = callPeerWebId(view, event);
        if (!webid) return '';
        return (await resolve(webid)) || UNRESOLVED_SIGNER + webid;
    }

    return {
        resolve, expectedFor,
        prefetch: (webid) => { if (isDmPeerWebId(webid)) _fetch(webid); },
        cached: (webid) => _cache.get(webid) || null,
    };
}
