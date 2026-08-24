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

// The outbound commands voice.js emits that this engine carries.
export const SIGNAL_CMDS = new Set(['voice_invite', 'voice_answer', 'ice_candidate', 'voice_hangup']);

export function createWebCalls({ pod, notify, handleEvent, getSelfWebId, getDisplayName, peerPodRoot }) {
    let _unsub = null;

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
        const sigs = await pod.podReadSignals();
        for (const { url, signal } of sigs) {
            try {
                if (signal && signal.type) handleEvent(signal);
            } catch (err) {
                console.warn('[webcalls] signal handling failed:', err);
            }
            await pod.podDeleteSignal(url);
        }
    }

    async function start() {
        const inbox = await pod.podEnsureCallInbox();
        await drainOnce();
        if (inbox && notify && notify.watchResource) {
            _unsub = notify.watchResource(inbox, () => { drainOnce().catch(() => {}); });
        }
        return inbox;
    }

    function stop() {
        if (_unsub) { try { _unsub(); } catch { /* ignore */ } _unsub = null; }
    }

    return { sendSignal, drainOnce, start, stop };
}
