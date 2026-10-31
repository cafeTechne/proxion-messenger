// crossmode.js — desktop DMs and presence with browser-build (WebID) contacts.
//
// A browser-build user is reached through their pod: DMs are signed, ratchet-
// encrypted envelopes dropped into proxion/dm-inbox/, presence is a public
// heartbeat. The desktop client runs that same protocol (webdm.js,
// webpresence.js) over its gateway's pod bridge (gwpod.js), as the WebID of the
// pod its gateway is connected to. Gateway contacts are untouched: only a
// command whose target is a WebID is routed here.
//
// Dependencies are injected so main.js stays the composition root and this is
// testable without a browser.

import { createGwPodBridge } from './gwpod.js';
import { createWebDm, createSenderVerifier, peerPodRootFromWebId, isDmPeerWebId } from './webdm.js';
import { createWebPresence } from './webpresence.js';
import { X25519_PUB_PATH } from './podio.js';

const _DM_CMDS = new Set(['local_dm', 'send_dm']);

// The WebID a DM command is addressed to, when it is one.
export function webIdTarget(cmd) {
    if (!cmd || !_DM_CMDS.has(cmd.cmd)) return null;
    const to = cmd.target_webid || cmd.to_webid || '';
    return isDmPeerWebId(to) ? to : null;
}

export function createCrossMode({
    send, handleEvent, getClientDid, getDisplayName, signEnvelope,
    verifyDmSig, verifyFanoutSig, verifyDeviceCert, e2e, persistMessage,
    isBlocked, isKnownSender, onMessageRequest, onUnverifiedDrop,
    getContacts, getMyPub, fetchAndCachePeerPub, onStateChange = () => {}, getSelfId = () => getClientDid(),
}) {
    let _dm = null, _presence = null, _startedFor = null;
    let _state = { ready: false, reason: 'unknown', webid: null, pod_root: null, conflict: false };

    const bridge = createGwPodBridge({ send, onInfo: (info) => { _onInfo(info).catch(() => {}); } });
    const notify = { watchResource: (url, cb) => bridge.watchResource(url, cb) };
    const via = () => ({ fetch: (u) => bridge.fetch(u), ownRoot: _state.pod_root });

    function _setState(patch) {
        _state = { ..._state, ...patch };
        try { onStateChange(_state); } catch { /* UI only */ }
    }

    async function _publishIdentity(info) {
        const io = bridge.io;
        // One device consumes a WebID's drop boxes: the one named in signer.json.
        // Taking it over is expected (this device signed in last), but say so.
        const prev = await io.podFetchPeerSigner(info.pod_root).catch(() => null);
        const conflict = !!(prev && prev.signer && prev.signer !== getClientDid());
        // No account did: a gateway-issued cert would let any registering socket
        // speak for the account (see docs), so the signer stands on its own.
        await io.podPublishSigner(getClientDid(), null);
        const pub = getMyPub();
        if (pub) {
            const url = info.pod_root + X25519_PUB_PATH;
            const res = await bridge.fetch(url, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ version: 1, pub }),
            }).catch(() => null);
            if (res && res.ok) await io.podPublishIdentityAcl(url);
        }
        return conflict;
    }

    async function _onInfo(info) {
        if (!info.ready) {
            _setState({ ready: false, reason: info.reason || 'not_ready', webid: null, pod_root: null });
            return;
        }
        _setState({ ready: true, reason: '', webid: info.webid, pod_root: info.pod_root });
        if (_startedFor === info.webid) { bridge.resubscribe(); _dm && _dm.drainOnce().catch(() => {}); return; }
        _startedFor = info.webid;
        const conflict = await _publishIdentity(info).catch(() => false);
        _setState({ conflict });
        _dm = createWebDm({
            pod: bridge.io, e2e, notify,
            handleEvent: (ev) => handleEvent(ev),
            getSelfWebId: () => _state.webid,
            getDisplayName,
            getMyDeviceId: () => getClientDid(),
            persistMessage, signEnvelope,
            verifySender: createSenderVerifier({
                verifyDmSig, verifyFanoutSig, verifyDeviceCert,
                fetchSigner: (root) => bridge.io.podFetchPeerSigner(root),
            }),
            isBlocked, isKnownSender, onMessageRequest, onUnverifiedDrop,
            strictSender: true,
        });
        _presence = createWebPresence({
            pod: bridge.io, notify,
            handleEvent: (ev) => handleEvent(ev),
            getContacts: () => (getContacts() || []).filter(isDmPeerWebId),
            peerPodRoot: (w) => peerPodRootFromWebId(w),
        });
        await _dm.start().catch(() => {});
        _presence.start();
    }

    // Route a DM command for a WebID contact through the pod. Returns true when
    // handled (the caller must not also send it to the gateway). The optimistic
    // copy is confirmed only when the drop landed; otherwise it stays pending and
    // the send-status retry applies.
    function route(cmd) {
        const to = webIdTarget(cmd);
        if (to) {
            if (_dm && _state.ready) {
                _dm.dropDm(cmd).then((ok) => {
                    if (ok) handleEvent({
                        type: 'message', message_id: cmd.message_id, thread_id: cmd.thread_id || to,
                        from_webid: getSelfId(), timestamp: new Date().toISOString(),
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
        _setState({ ready: false, reason: 'disconnected' });
        bridge.requestInfo();
    }

    return {
        handleEvent: (ev) => bridge.handleEvent(ev),
        route, onReconnect,
        requestInfo: () => bridge.requestInfo(),
        state: () => _state,
        isReady: () => !!(_state.ready && _dm),
        webId: () => _state.webid,
        fetchPeerPub: (peerId) => (_state.ready && fetchAndCachePeerPub
            ? fetchAndCachePeerPub(peerId, peerPodRootFromWebId(peerId), via())
            : Promise.resolve(false)),
        subscribeContact: (w) => { if (_presence && isDmPeerWebId(w)) _presence.subscribeContact(w); },
        drainOnce: () => (_dm ? _dm.drainOnce() : Promise.resolve()),
        bridge,
    };
}
