// crossmode.js: desktop DMs, presence and calls with browser-build (WebID) contacts.
//
// A browser-build user is reached through their pod: DMs are signed, ratchet-
// encrypted envelopes dropped into proxion/dm-inbox/, presence is a public
// heartbeat, call signaling goes through proxion/call-inbox/. The desktop client
// runs that same protocol (webdm.js, webpresence.js, webcalls.js) over its
// gateway's pod bridge (gwpod.js), as the WebID of the
// pod its gateway is connected to. Gateway contacts are untouched: only a
// command whose target is a WebID is routed here.
//
// Dependencies are injected so main.js stays the composition root and this is
// testable without a browser.

import { createGwPodBridge } from './gwpod.js';
import { createWebDm, createSenderVerifier, peerPodRootFromWebId, isDmPeerWebId } from './webdm.js';
import { createWebPresence } from './webpresence.js';
import { createWebCalls, createCallSigners, SIGNAL_CMDS } from './webcalls.js';
import { X25519_PUB_PATH } from './podio.js';

const _DM_CMDS = new Set(['local_dm', 'send_dm']);

// The WebID a DM command is addressed to, when it is one.
export function webIdTarget(cmd) {
    if (!cmd || !_DM_CMDS.has(cmd.cmd)) return null;
    const to = cmd.target_webid || cmd.to_webid || '';
    return isDmPeerWebId(to) ? to : null;
}

// The WebID a 1:1 call signal (voice.js) is addressed to, when it is one.
export function callWebIdTarget(cmd) {
    if (!cmd || !SIGNAL_CMDS.has(cmd.cmd)) return null;
    return isDmPeerWebId(cmd.target_webid) ? cmd.target_webid : null;
}

export function createCrossMode({
    send, handleEvent, getClientDid, getDisplayName, signEnvelope,
    verifyDmSig, verifyFanoutSig, verifyDeviceCert, e2e, persistMessage, receipts,
    isBlocked, isKnownSender, onMessageRequest, onUnverifiedDrop,
    getContacts, getMyPub, fetchAndCachePeerPub, onStateChange = () => {}, getSelfId = () => getClientDid(),
    isAllowedCaller = isKnownSender,
}) {
    let _dm = null, _presence = null, _calls = null, _callActive = false, _startedFor = null;
    let _generation = 0, _starting = null, _startingFor = null;
    let _state = { ready: false, reason: 'unknown', webid: null, pod_root: null, conflict: false };

    const bridge = createGwPodBridge({ send, onInfo: (info) => { _onInfo(info).catch(() => {}); } });
    const notify = {
        watchResource: (url, cb, opts) => bridge.watchResource(url, cb, opts),
        setFast: (url, fast) => bridge.setFast(url, fast),
    };
    // The signer.json key a WebID contact's calls must be signed by (callsec.js).
    const callSigners = createCallSigners({
        fetchSigner: (w) => bridge.io.podFetchPeerSigner(peerPodRootFromWebId(w)),
    });
    const via = () => ({ fetch: (u) => bridge.fetch(u), ownRoot: _state.pod_root });

    function _setState(patch) {
        _state = { ..._state, ...patch };
        try { onStateChange(_state); } catch { /* UI only */ }
    }

    async function _publishIdentity(info, current) {
        const io = bridge.io;
        const pub = getMyPub();
        if (!pub) throw new Error('encryption key unavailable');
        // One device consumes a WebID's drop boxes: the one named in signer.json.
        // Taking it over is expected (this device signed in last), but say so.
        const prev = await io.podFetchPeerSigner(info.pod_root).catch(() => null);
        if (!current()) throw new Error('session changed');
        const conflict = !!(prev && prev.signer && prev.signer !== getClientDid());
        // No account did: a gateway-issued cert would let any registering socket
        // speak for the account (see docs), so the signer stands on its own.
        if (!await io.podPublishSigner(getClientDid(), null, { requirePublic: true })) throw new Error('signer publication failed');
        if (!current()) throw new Error('session changed');
        const url = info.pod_root + X25519_PUB_PATH;
        const res = await bridge.fetch(url, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ version: 1, pub }),
        }).catch(() => null);
        if (!current()) throw new Error('session changed');
        if (!res?.ok || !await io.podPublishIdentityAcl(url)) throw new Error('encryption key publication failed');
        return conflict;
    }

    function _stop() {
        _generation++;
        _dm?.stop(); _presence?.stop(); _calls?.stop();
        _dm = _presence = _calls = null;
        _startedFor = _startingFor = _starting = null;
    }

    function _onInfo(info) {
        if (!info.ready) {
            _stop();
            _setState({ ready: false, started: false, reason: info.reason || 'not_ready', webid: null, pod_root: null, conflict: false });
            return Promise.resolve();
        }
        const key = `${info.webid}\n${info.pod_root}`;
        if (_startingFor === key) return _starting;
        if (_startedFor === key) {
            bridge.resubscribe();
            _dm?.drainOnce().catch(() => {});
            _calls?.drainOnce().catch(() => {});
            return Promise.resolve();
        }
        _stop();
        const generation = _generation;
        _setState({ ready: false, started: false, reason: 'identity_pending', webid: info.webid, pod_root: info.pod_root, conflict: false });
        _startingFor = key;
        const starting = _start(info, generation).then(() => {
            if (generation === _generation) _startedFor = key;
        }).catch(() => {
            if (generation === _generation) {
                _stop();
                _setState({ ready: false, started: false, reason: 'identity_failed' });
            }
        }).finally(() => {
            if (_starting === starting) _starting = _startingFor = null;
        });
        _starting = starting;
        return starting;
    }

    async function _start(info, generation) {
        const current = () => generation === _generation;
        const conflict = await _publishIdentity(info, current);
        if (!current()) return;
        const dispatch = (ev) => { if (current()) handleEvent(ev); };
        _dm = createWebDm({
            pod: bridge.io, e2e, notify,
            handleEvent: dispatch,
            getSelfWebId: () => info.webid,
            getDisplayName,
            getMyDeviceId: () => getClientDid(),
            persistMessage, receipts, signEnvelope,
            verifySender: createSenderVerifier({
                verifyDmSig, verifyFanoutSig, verifyDeviceCert,
                fetchSigner: (root) => bridge.io.podFetchPeerSigner(root),
            }),
            isBlocked, isKnownSender, onMessageRequest, onUnverifiedDrop,
            strictSender: true,
        });
        _presence = createWebPresence({
            pod: bridge.io, notify,
            handleEvent: dispatch,
            getContacts: () => (getContacts() || []).filter(isDmPeerWebId),
            peerPodRoot: (w) => peerPodRootFromWebId(w),
        });
        _calls = createWebCalls({
            pod: bridge.io, notify,
            handleEvent: dispatch,
            getSelfWebId: () => info.webid,
            getDisplayName,
            peerPodRoot: (w) => peerPodRootFromWebId(w),
            isBlocked,
            // Only contacts ring the desktop, as with gateway calls.
            isAllowedCaller: (w) => !!(w && isAllowedCaller(w)),
        });
        _calls.setActive(_callActive);
        await _dm.start().catch(() => {});
        if (!current()) return;
        _presence.start();
        await _calls.start().catch(() => {});
        if (!current()) return;
        _setState({ ready: true, reason: '', conflict, started: true });   // identity published: pod rooms may start (crossrooms.js)
    }

    // A call signal for a WebID contact goes to their call-inbox. No gateway
    // cert rides along: the signer.json this device publishes names its key
    // directly, and the far side verifies against that. A call that cannot be
    // placed ends at once instead of ringing out for 30 seconds.
    function _routeCall(cmd, to) {
        const { fp_cert: _cert, ...signal } = cmd;
        const unavailable = () => {
            if (cmd.cmd === 'voice_invite') handleEvent({ type: 'voice_unavailable', session_id: cmd.session_id, target_webid: to });
        };
        if (!_calls || !_state.ready) { unavailable(); return; }
        _calls.sendSignal(signal).then((ok) => { if (!ok) unavailable(); }).catch(unavailable);
    }

    // Route a DM command for a WebID contact through the pod. Returns true when
    // handled (the caller must not also send it to the gateway). The optimistic
    // copy is confirmed only when the drop landed; otherwise it stays pending and
    // the send-status retry applies.
    function route(cmd) {
        const callTo = callWebIdTarget(cmd);
        if (callTo) { _routeCall(cmd, callTo); return true; }
        const to = webIdTarget(cmd);
        if (to) {
            if (_dm && _state.ready) {
                const generation = _generation, self = getSelfId();
                _dm.dropDm(cmd).then((ok) => {
                    if (ok && generation === _generation) handleEvent({
                        type: 'message', message_id: cmd.message_id, thread_id: cmd.thread_id || to,
                        from_webid: self, timestamp: new Date().toISOString(),
                        source: 'local_dm', _podEcho: true,
                    });
                }).catch(() => {});
            }
            return true;
        }
        // Self-sync copies for a WebID thread: our linked devices do not take part
        // in cross-mode threads (one consuming device per WebID).
        if (cmd && cmd.cmd === 'send_dm_fanout' && Array.isArray(cmd.fanout)
            && cmd.fanout.some((e) => e && (isDmPeerWebId(e.to_webid) || isDmPeerWebId(e.payload && e.payload.thread_peer)))) {
            return true;
        }
        return false;
    }

    // The socket dropped or reconnected: fail requests in flight, ask again.
    function onReconnect() {
        bridge.reset();
        _onInfo({ ready: false, reason: 'disconnected' });
        bridge.requestInfo();
    }

    return {
        handleEvent: (ev) => {
            const handled = bridge.handleEvent(ev);
            if ((ev?.type === 'pod_fetch_result' && ev.error === 'not_consumer')
                || (ev?.type === 'pod_watch_refused' && ev.reason === 'not_consumer')) {
                bridge.reset();
                _onInfo({ ready: false, reason: 'not_consumer' });
            }
            return handled;
        },
        route, onReconnect,
        requestInfo: () => bridge.requestInfo(),
        state: () => _state,
        isReady: () => !!(_state.ready && _dm),
        webId: () => _state.webid,
        fetchPeerPub: (peerId) => (_state.ready && fetchAndCachePeerPub
            ? fetchAndCachePeerPub(peerId, peerPodRootFromWebId(peerId), via())
            : Promise.resolve(false)),
        subscribeContact: (w) => {
            if (_presence && isDmPeerWebId(w)) _presence.subscribeContact(w);
            if (_state.ready) callSigners.prefetch(w);
        },
        // Ringing or in a call: poll the call-inbox every second.
        setCallActive: (active) => { _callActive = !!active; if (_calls) _calls.setActive(_callActive); },
        expectedCallSigner: (view, event) => callSigners.expectedFor(view, event),
        drainOnce: () => (_dm ? _dm.drainOnce() : Promise.resolve()),
        dropDm: payload => (_state.ready && _dm ? _dm.dropDm(payload) : Promise.resolve(false)),
        dropFanout: payload => (_state.ready && _dm ? _dm.dropFanout(payload) : Promise.resolve(false)),
        drainCalls: () => (_calls ? _calls.drainOnce() : Promise.resolve()),
        bridge,
    };
}
