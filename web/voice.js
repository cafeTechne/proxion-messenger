// Voice + WebRTC subsystem (1:1 calls + group voice channels), extracted from
// main.js (R40). createVoice(deps) owns voice state in `state` and returns the
// handlers main.js wires into the WS dispatch and call/mute/leave buttons.
import { t } from './i18n.js';
import { escHtml } from './util.js';
import { extractFingerprint, signFingerprint, classifyPeerSdp } from './callsec.js';
import { senderCap, canEnableVideo } from './callquality.js';
import { mediaConstraints } from './devices.js';
import { classifyConnection, deriveStats } from './callstats.js';

const _dev = (k) => { try { return localStorage.getItem(k) || ''; } catch { return ''; } };

export const CALL_TIMEOUT_MS = 30000;
export const CallState = Object.freeze({
    IDLE: 'idle',
    CALLING: 'calling',
    RINGING: 'ringing',
    CONNECTED: 'connected',
    ENDING: 'ending',
});

// RMS of AnalyserNode time-domain samples (bytes 0-255 centered at 128), normalized
// to ~0-1. Used by the speaking detector to decide who's talking. Pure + testable.
export function audioLevel(data) {
    if (!data || !data.length) return 0;
    let sum = 0;
    for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128;
        sum += v * v;
    }
    return Math.sqrt(sum / data.length);
}

export function createVoice(deps) {
    const { showToast, renderMessage, showOsNotification, sendCmd, playNotificationSound, normalizeRelayThreadId, stopScreenShare, getSocket, getActiveView, getSelfWebId, getTurnUrl, getTurnSecret, getLocalDmPeers, getCurrentRoomMembers, getIsSharing, getIdentityPrivKey, getClientDid, getExpectedPeerDid } = deps;
    const state = {
            currentCall: null,
            localStream: null,
            pc: null,
            peerConnections: {},
            peerAudioElements: {},
            _channelSessionIds: {},
            _channelParticipants: {},
            _turnIceServer: null,
            currentCallSessionId: null,
            isMuted: false,
            _pendingCandidates: [],
            _remoteDescSet: false,
            _callTimeoutId: null,
            _callState: CallState.IDLE,
            callStartTime: null,
            callTimerInterval: null,
            _inVoiceChannel: null,
            ringOscillator: null,
            videoEnabled: false,     // is our camera track live in this call
            _videoSender: null,      // the pre-negotiated video sender (camera/screen go here)
            _cameraTrack: null,      // our camera track when video is on
            _verifyState: 'unverified',  // 'verified' | 'unverified' | (mismatch → call refused)
            _peerVideoSenders: {},   // group: per-peer video sender for camera/screen fan-out
            peerVideoElements: {},   // group: per-peer <video> tile
            _peerRemoteStreams: {},  // group: per-peer accumulated remote MediaStream
    };

    // Peers currently sending us live video (R83): bounds how many share at once.
    function _activeRemoteVideoCount() {
        let n = 0;
        for (const s of Object.values(state._peerRemoteStreams || {})) {
            if (s && s.getVideoTracks && s.getVideoTracks().some(tr => tr.readyState === 'live')) n++;
        }
        return n;
    }

    // The user's quality preference: 'auto' | 'high' | 'standard' | 'saver'.
    function getQualityProfile() {
        try { return localStorage.getItem('proxion_call_quality') || 'auto'; } catch { return 'auto'; }
    }
    function _participantCount() {
        return Object.keys(state._channelParticipants || {}).length + 1;
    }
    // Apply the adaptive cap to one sender for the current call context (R81 P2).
    async function _capSender(sender) {
        try {
            const cap = senderCap({
                profile: getQualityProfile(),
                isGroup: !!state._inVoiceChannel,
                participantCount: _participantCount(),
                kind: (getIsSharing && getIsSharing()) ? 'screen' : 'camera',
            });
            const p = sender.getParameters();
            p.encodings = p.encodings && p.encodings.length ? p.encodings : [{}];
            p.encodings[0].maxBitrate = cap.maxBitrate;
            p.encodings[0].maxFramerate = cap.maxFramerate;
            await sender.setParameters(p);
        } catch (_) { /* best-effort; some browsers reject mid-call */ }
    }
    // Re-apply the cap to every active video sender (on count change, camera/screen
    // toggle, or a quality-setting change). No renegotiation.
    async function recapSenders() {
        for (const s of _allVideoSenders()) await _capSender(s);
    }
    // R81 S: poll getStats and surface a call-health level (good/fair/poor). One timer
    // covers the 1:1 pc and every group peer connection.
    function _startStatsMonitor() {
        if (state._statsTimer) return;
        state._statsPrev = {};
        state._statsTimer = setInterval(() => { _pollStats().catch(() => {}); }, 2500);
    }
    function _stopStatsMonitor() {
        if (state._statsTimer) { clearInterval(state._statsTimer); state._statsTimer = null; }
        state._statsPrev = {};
        const el = document.getElementById('vw-conn'); if (el) el.style.display = 'none';
    }
    async function _pollStats() {
        const collect = async (pc) => { const out = []; (await pc.getStats()).forEach(s => out.push(s)); return out; };
        if (state.pc && state.pc.getStats) {
            const d = deriveStats(await collect(state.pc), state._statsPrev['_1to1']);
            state._statsPrev['_1to1'] = d;
            _setConnIndicator(classifyConnection(d));
        }
        for (const [webid, pc] of Object.entries(state.peerConnections)) {
            if (!pc || !pc.getStats) continue;
            const d = deriveStats(await collect(pc), state._statsPrev[webid]);
            state._statsPrev[webid] = d;
            const pill = document.querySelector?.('#voice-channel-participants [data-vc-webid="' + webid + '"]');
            if (pill) pill.dataset.quality = classifyConnection(d);
        }
    }
    function _setConnIndicator(level) {
        const el = document.getElementById('vw-conn');
        if (!el) return;
        const color = { good: '#4ade80', fair: '#fbbf24', poor: '#f87171' }[level] || '#94a3b8';
        // Literal t() calls so the i18n checker sees these keys referenced.
        const label = level === 'good' ? t('voice.conn.good')
            : level === 'fair' ? t('voice.conn.fair') : t('voice.conn.poor');
        el.style.display = state._callState === CallState.CONNECTED ? '' : 'none';
        el.style.color = color;
        el.textContent = '● ' + label;
        el.title = label;
    }

    // Persist a new quality preference and apply it to the live call immediately.
    function setQualityProfile(name) {
        try { localStorage.setItem('proxion_call_quality', name); } catch { /* quota */ }
        recapSenders();
    }

        function updateVoiceChannels(roomId) {
            // Voice channels not yet implemented — keep section hidden
        }

        async function joinVoice(roomId) {
            getSocket().send(JSON.stringify({cmd: "join_voice_channel", room_id: roomId}));
            state._inVoiceChannel = roomId;
            _startStatsMonitor();
            const leaveBtn = document.getElementById("leave-voice-channel-btn");
            if (leaveBtn) leaveBtn.style.display = "";
            _showChannelPanel();
            _renderChannelPanel();
        }

        function leaveVoiceChannel() {
            if (!state._inVoiceChannel) return;
            getSocket().send(JSON.stringify({cmd: "leave_voice_channel", room_id: state._inVoiceChannel}));
            state._inVoiceChannel = null;
            const leaveBtn = document.getElementById("leave-voice-channel-btn");
            if (leaveBtn) leaveBtn.style.display = "none";
            // Close all peer connections in the channel
            for (const peerId of Object.keys(state.peerConnections)) {
                try { state.peerConnections[peerId].close(); } catch (_) {}
                delete state.peerConnections[peerId];
            }
            for (const peerId of Object.keys(state.peerAudioElements)) {
                state.peerAudioElements[peerId].srcObject = null;
                delete state.peerAudioElements[peerId];
            }
            // Tear down group video: tiles, senders, our own camera + self-view.
            for (const peerId of Object.keys(state.peerVideoElements)) {
                try { state.peerVideoElements[peerId].srcObject = null; state.peerVideoElements[peerId].remove(); } catch (_) {}
                delete state.peerVideoElements[peerId];
            }
            state._peerVideoSenders = {};
            if (state._cameraTrack) { try { state._cameraTrack.stop(); } catch (_) {} state._cameraTrack = null; }
            if (getIsSharing && getIsSharing()) { try { stopScreenShare(); } catch (_) {} }
            state.videoEnabled = false;
            _renderVideo('vw-local-video', null);
            const vgrid = document.getElementById('voice-channel-videos');
            if (vgrid) vgrid.style.display = 'none';
            state._channelSessionIds = {};
            // Release the microphone — otherwise the OS/browser recording
            // indicator stays lit after leaving the channel.
            if (state.localStream) { state.localStream.getTracks().forEach(tr => tr.stop()); state.localStream = null; }
            state._mediaDenied = false;
            _stopStatsMonitor();
            _speaking.stopAll();
            _hideChannelPanel();
            showToast(t('voice.left'));
        }

        function _callerDisplayName(webid) {
            const dmPeer = Object.values(getLocalDmPeers()).find(p => p.peer_webid === webid);
            if (dmPeer && dmPeer.display_name) return dmPeer.display_name;
            const member = getCurrentRoomMembers().find(m => m.webid === webid);
            if (member && member.display_name) return member.display_name;
            return webid.slice(0, 28);
        }

        function showVoiceBanner(invite) {
            state.currentCall = invite;
            setCallState(CallState.RINGING);
            const banner = document.getElementById("voice-banner");
            document.getElementById("voice-msg").innerText =
                `Incoming call from ${_callerDisplayName(invite.caller_webid)}`;
            banner.style.display = "flex";
            playRingTone();
            const _ringCaller = invite.caller_webid;
            setTimeout(() => {
                if (state._callState === CallState.RINGING) {
                    banner.style.display = "none";
                    state.currentCall = null;
                    setCallState(CallState.IDLE);
                    stopRingTone();
                    // R82 W3: an unanswered ring leaves a missed-call trace in-app.
                    showToast(t('voice.missedFrom', { peer: _callerDisplayName(_ringCaller) }));
                }
            }, 30000);
        }

        // R82 W1: the gateway could not reach the callee (offline / unreachable).
        function handleVoiceUnavailable(event) {
            if (state._callState === CallState.IDLE) return;
            stopRingTone();
            showToast(t('voice.unavailable'));
            _doHangup();
        }

        async function getMedia() {
            if (state.localStream) return state.localStream;
            // If the mic was already denied this session, don't re-prompt or
            // re-toast on every peer connection (group calls call this per peer).
            if (state._mediaDenied) return null;
            try {
                // D1: browser-native call-quality DSP (noise suppression, echo
                // cancellation, auto gain). Falls back gracefully if a browser
                // ignores unknown constraints. Audio only here — video is captured
                // separately via enableCamera and attached to the pre-negotiated
                // video sender, so it can be toggled without renegotiation.
                state.localStream = await navigator.mediaDevices.getUserMedia(
                    mediaConstraints({ micId: _dev('proxion_mic_id'), video: false }));
            } catch (err) {
                state._mediaDenied = true;
                showToast(t('voice.micError', { error: (err && err.name ? err.name : err) }), "error");
            }
            return state.localStream;
        }

        // Attach a media stream to a <video> element in the call widget. `mirror`
        // flips the local self-view so it reads like a mirror.
        function _renderVideo(elId, stream, { mirror = false, muted = false } = {}) {
            const el = document.getElementById(elId);
            if (!el) return;
            el.srcObject = stream || null;
            el.muted = muted;
            el.style.transform = mirror ? 'scaleX(-1)' : '';
            el.style.display = stream ? '' : 'none';
            if (stream) el.play?.().catch(() => {});
        }

        // Sign the DTLS fingerprint from our own local SDP so the peer can prove the
        // media channel is really ours (defeats a gateway SDP swap). Returns
        // { fp_sig, fp_signer } or {} if we cannot sign (no identity key).
        async function _signLocalFingerprint(role) {
            try {
                const priv = getIdentityPrivKey?.();
                const did = getClientDid?.();
                if (!priv || !did || !state.pc?.localDescription?.sdp) return {};
                const fingerprint = extractFingerprint(state.pc.localDescription.sdp);
                if (!fingerprint) return {};
                const fp_sig = await signFingerprint({
                    fingerprint, sessionId: state.currentCallSessionId || '', role, privKey: priv,
                });
                return { fp_sig, fp_signer: did };
            } catch { return {}; }
        }

        // Verify a received offer/answer SDP against the expected contact identity.
        // Returns true to proceed, false if the call must be refused (MitM).
        async function _verifyPeerSdp(sdp, role, event) {
            const _exp = getExpectedPeerDid?.(getActiveView(), event) || '';
            const verdict = await classifyPeerSdp({
                sdp,
                role,
                signatureB64: event?.fp_sig || '',
                signerDid: event?.fp_signer || '',
                expectedDid: _exp,
            });
            // Advisory, not blocking: we show Verified only when the peer's identity
            // signature checks out against the identity we know them by, otherwise
            // Unverified. The call is never refused, because a call's signing identity
            // is not yet bound to the contact roster across gateways (a browser signs,
            // but a federated contact is known by their gateway identity). Media stays
            // DTLS-SRTP encrypted regardless. See docs/CALLS.md.
            state._verifyState = verdict === 'verified' ? 'verified' : 'unverified';
            _updateVerifyBadge();
            return true;
        }

        function _updateVerifyBadge() {
            const el = document.getElementById('vw-verified');
            if (!el) return;
            const v = state._verifyState === 'verified';
            el.textContent = v ? t('voice.verified') : t('voice.unverified');
            el.title = v ? t('voice.verifiedHint') : t('voice.unverifiedHint');
            el.dataset.state = state._verifyState;
            el.style.display = state._callState === CallState.CONNECTED ? '' : 'none';
        }

        // Turn our camera on/off mid-call by swapping the track in the pre-negotiated
        // video sender — no renegotiation needed. Returns the new enabled state.
        // Every video sender in the current call: the 1:1 sender plus each group peer's.
        function _allVideoSenders() {
            const out = [];
            if (state._videoSender) out.push(state._videoSender);
            for (const s of Object.values(state._peerVideoSenders)) if (s) out.push(s);
            return out;
        }

        async function toggleCamera() {
            const senders = _allVideoSenders();
            if (!senders.length) return state.videoEnabled;
            // Group calls show the self-view in the channel grid; 1:1 in the call widget.
            const selfView = state._inVoiceChannel ? 'vc-local-video' : 'vw-local-video';
            if (state.videoEnabled) {
                try { state._cameraTrack?.stop(); } catch (_) {}
                state._cameraTrack = null;
                for (const s of senders) { try { await s.replaceTrack(null); } catch (_) {} }
                state.videoEnabled = false;
                _renderVideo(selfView, null);
            } else {
                // R83: in a group call, cap how many people share video at once so a
                // large mesh call stays within bandwidth. Audio is unaffected.
                if (state._inVoiceChannel && !canEnableVideo(_activeRemoteVideoCount())) {
                    showToast(t('voice.videoFull'));
                    return false;
                }
                let cam;
                try {
                    const camId = _dev('proxion_cam_id');
                    cam = await navigator.mediaDevices.getUserMedia({ video: camId ? { deviceId: { exact: camId } } : true, audio: false });
                } catch (err) { showToast(t('voice.cameraError', { error: (err && err.name) || err }), 'error'); return false; }
                const track = cam.getVideoTracks()[0];
                if (!track) return false;
                state._cameraTrack = track;
                for (const s of senders) { try { await s.replaceTrack(track); } catch (_) {} }
                state.videoEnabled = true;
                _renderVideo(selfView, cam, { mirror: true, muted: true });
                const grid = document.getElementById('voice-channel-videos');
                if (state._inVoiceChannel && grid) grid.style.display = 'flex';
                track.onended = () => { if (state.videoEnabled) toggleCamera(); };
            }
            _updateCallUI();
            return state.videoEnabled;
        }

        async function getTurnCredentials(username, secret) {
            const enc = new TextEncoder();
            const key = await crypto.subtle.importKey(
                "raw", enc.encode(secret),
                { name: "HMAC", hash: "SHA-1" },
                false, ["sign"]
            );
            const signature = await crypto.subtle.sign("HMAC", key, enc.encode(username));
            const b64 = btoa(String.fromCharCode(...new Uint8Array(signature)));
            return b64;
        }

        function setCallState(newState) {
            const valid = {
                [CallState.IDLE]: [CallState.CALLING, CallState.RINGING],
                [CallState.CALLING]: [CallState.CONNECTED, CallState.ENDING, CallState.IDLE],
                [CallState.RINGING]: [CallState.CONNECTED, CallState.ENDING, CallState.IDLE],
                [CallState.CONNECTED]: [CallState.ENDING],
                [CallState.ENDING]: [CallState.IDLE],
            };
            if (!valid[state._callState]?.includes(newState)) return;
            state._callState = newState;
            _updateCallUI();
        }

        function _updateCallUI() {
            const widget = document.getElementById("voice-widget");
            if (!widget) return;
            const connected = state._callState === CallState.CONNECTED;
            const active = connected
                || state._callState === CallState.CALLING
                || state._callState === CallState.RINGING;
            widget.style.display = active ? "flex" : "none";
            const ssBtn = document.getElementById("screenshare-btn");
            if (ssBtn) ssBtn.style.display = connected ? "flex" : "none";
            const camBtn = document.getElementById("camera-btn");
            if (camBtn) {
                camBtn.style.display = connected ? "flex" : "none";
                camBtn.classList.toggle("vw-active", state.videoEnabled);
                camBtn.setAttribute("aria-pressed", state.videoEnabled ? "true" : "false");
            }
            const fsBtn = document.getElementById("vw-fullscreen-btn");
            if (fsBtn) {
                const hasVideo = state.videoEnabled || !!(state.remoteStream &&
                    state.remoteStream.getVideoTracks && state.remoteStream.getVideoTracks().length);
                fsBtn.style.display = connected && hasVideo ? "flex" : "none";
            }
            if (!connected && getIsSharing()) stopScreenShare();
            // Privacy: an always-visible indicator whenever our camera or screen is live.
            const capEl = document.getElementById("vw-capture-indicator");
            if (capEl) {
                const sharing = getIsSharing();
                const on = connected && (state.videoEnabled || sharing);
                capEl.style.display = on ? "" : "none";
                capEl.textContent = state.videoEnabled && sharing ? t('voice.capturingBoth')
                    : sharing ? t('voice.capturingScreen')
                        : state.videoEnabled ? t('voice.capturingCamera') : "";
            }
            _syncRemoteVideoVisibility();
            _updateVerifyBadge();
            const statusEl = document.getElementById("vw-status");
            if (statusEl && !connected) {
                statusEl.textContent = state._callState === CallState.CALLING ? "Calling..." : "Incoming...";
            }
        }

        // Show the remote video surface only when a live remote video track is present,
        // so a voice-only call does not show a black rectangle.
        function _syncRemoteVideoVisibility() {
            const el = document.getElementById('vw-remote-video');
            if (!el) return;
            const hasVideo = !!(state.remoteStream &&
                state.remoteStream.getVideoTracks().some(tr => tr.readyState === 'live' && !tr.muted));
            el.style.display = hasVideo ? '' : 'none';
        }

        function _startCallTimeout() {
            if (state._callTimeoutId) clearTimeout(state._callTimeoutId);
            state._callTimeoutId = setTimeout(() => {
                if (state._callState === CallState.CALLING) {
                    showToast(t('voice.callNotAnswered'));
                    _doHangup();
                }
            }, CALL_TIMEOUT_MS);
        }

        function _clearCallTimeout() {
            if (state._callTimeoutId) clearTimeout(state._callTimeoutId);
            state._callTimeoutId = null;
        }

        async function _setRemoteAndDrainCandidates(sdp, type) {
            await state.pc.setRemoteDescription({ type, sdp });
            state._remoteDescSet = true;
            for (const c of state._pendingCandidates) {
                getSocket().send(JSON.stringify({
                    cmd: "ice_candidate",
                    cert_id: getActiveView()?.id,
                    session_id: state.currentCallSessionId,
                    target_webid: state._callPeerWebid || undefined,
                    candidate: c.candidate,
                    sdp_mid: c.sdpMid,
                    sdp_mline_index: c.sdpMLineIndex
                }));
            }
            state._pendingCandidates = [];
        }

        async function _getIceServers() {
            const iceServers = [{ urls: 'stun:stun.l.google.com:19302' }];
            if (!state._turnIceServer) {
                try {
                    const _tc = await fetch('/turn-credentials').then(r => r.json());
                    if (_tc && _tc.urls && _tc.urls.length > 0) {
                        state._turnIceServer = { urls: _tc.urls, username: _tc.username, credential: _tc.credential };
                    }
                } catch (_) {}
            }
            if (state._turnIceServer) {
                iceServers.push(state._turnIceServer);
            } else if (getTurnUrl() && getTurnSecret()) {
                const timestamp = Math.floor(Date.now() / 1000) + 86400;
                const username = `${timestamp}:${getSelfWebId()}`;
                const credential = await getTurnCredentials(username, getTurnSecret());
                iceServers.push({ urls: getTurnUrl(), username, credential });
            }
            return iceServers;
        }

        async function initWebRTCForPeer(targetWebid, sessionId, isCaller = false, sdpOffer = null, offerMeta = null) {
            if (state.peerConnections[targetWebid]) {
                try { state.peerConnections[targetWebid].close(); } catch (_) {}
                delete state.peerConnections[targetWebid];
            }
            const iceServers = await _getIceServers();
            const peerPc = new RTCPeerConnection({ iceServers });
            state.peerConnections[targetWebid] = peerPc;
            if (sessionId) state._channelSessionIds[targetWebid] = sessionId;

            const stream = await getMedia();
            if (stream) stream.getTracks().forEach(tr => peerPc.addTrack(tr, stream));
            // Per-peer video m-line up front so camera/screen fan out via replaceTrack
            // with no renegotiation (R80 C1). The transceiver is idle and free until
            // someone actually shares, so we negotiate it regardless of call size — the
            // concurrent-video CAP (R83) bounds how many send at once, not who can.
            const vt = peerPc.addTransceiver('video', { direction: 'sendrecv' });
            state._peerVideoSenders[targetWebid] = vt.sender;
            _capSender(vt.sender);
            // If our camera is already live, send it to this peer immediately.
            if (state.videoEnabled && state._cameraTrack) {
                try { vt.sender.replaceTrack(state._cameraTrack); } catch (_) {}
            }

            peerPc.ontrack = (event) => {
                // Accumulate tracks per peer: replaceTrack-added video/screen tracks
                // carry no MediaStream, so event.streams is empty for them.
                let s = state._peerRemoteStreams[targetWebid];
                if (!s) { s = new MediaStream(); state._peerRemoteStreams[targetWebid] = s; }
                try { s.addTrack(event.track); } catch (_) { /* dup */ }
                _renderPeerVideo(targetWebid, s);
                _updateChannelParticipantUI(targetWebid, "connected");
                _speaking.attach(targetWebid, s);
            };

            peerPc.onicecandidate = (e) => {
                if (!e.candidate) return;
                getSocket()?.send(JSON.stringify({
                    cmd: "ice_candidate",
                    target_webid: targetWebid,
                    session_id: state._channelSessionIds[targetWebid] || sessionId || "",
                    candidate: e.candidate.candidate,
                    sdp_mid: e.candidate.sdpMid,
                    sdp_mline_index: e.candidate.sdpMLineIndex,
                }));
            };

            peerPc.oniceconnectionstatechange = () => {
                const _st = peerPc.iceConnectionState;
                _updateChannelParticipantUI(targetWebid, _st);  // updates the status dot
                if (_st === "failed") {
                    // Surface it (H4): a silently-failing call is the worst case. Toast
                    // once per failure episode (reset on recovery) so ICE flapping
                    // doesn't spam.
                    if (!peerPc._proxionFailToasted) {
                        peerPc._proxionFailToasted = true;
                        showToast(t('voice.connectionTrouble', { peer: targetWebid.slice(0, 20) }), "error");
                    }
                    // Recovery: restartIce() alone is a no-op here — it only flags the
                    // next offer, and this codebase renegotiates by tearing down and
                    // rebuilding the peer on a fresh voice_invite (initWebRTCForPeer),
                    // not via an onnegotiationneeded re-offer. So the CALLER side —
                    // stable per pair by the "one offer per pair" join rule — rebuilds,
                    // which re-sends voice_invite and restarts ICE end to end. The
                    // callee waits for that offer to avoid glare. Cap attempts so a
                    // dead network doesn't loop forever; the counter rides across
                    // rebuilds and resets once the pair connects.
                    const _tries = peerPc._proxionReconnects || 0;
                    if (isCaller && _tries < 3) {
                        initWebRTCForPeer(targetWebid, state._channelSessionIds[targetWebid] || sessionId, true)
                            .then(newPc => { if (newPc) newPc._proxionReconnects = _tries + 1; })
                            .catch(() => {});
                    }
                } else if (_st === "connected" || _st === "completed") {
                    peerPc._proxionFailToasted = false;
                    peerPc._proxionReconnects = 0;
                }
            };

            if (isCaller) {
                const offer = await peerPc.createOffer();
                await peerPc.setLocalDescription(offer);
                const fp = await _signPeerFingerprint(peerPc, sessionId || state._channelSessionIds[targetWebid] || '', 'offer');
                getSocket()?.send(JSON.stringify({
                    cmd: "voice_invite",
                    target_webid: targetWebid,
                    sdp_offer: offer.sdp,
                    channel_id: state._inVoiceChannel || "",
                    ...fp,
                }));
            } else if (sdpOffer) {
                // Authenticate the co-member's media channel where we know their identity.
                if (!(await _verifyGroupSdp(targetWebid, sdpOffer, 'offer', offerMeta || {}))) {
                    try { peerPc.close(); } catch (_) {}
                    delete state.peerConnections[targetWebid];
                    return null;
                }
                await peerPc.setRemoteDescription({ type: "offer", sdp: sdpOffer });
                const answer = await peerPc.createAnswer();
                await peerPc.setLocalDescription(answer);
                const fp = await _signPeerFingerprint(peerPc, sessionId || '', 'answer');
                getSocket()?.send(JSON.stringify({
                    cmd: "voice_answer",
                    target_webid: targetWebid,
                    session_id: sessionId,
                    sdp_answer: answer.sdp,
                    ...fp,
                }));
            }
            return peerPc;
        }

        // Sign a specific peer connection's local DTLS fingerprint (group path).
        async function _signPeerFingerprint(peerPc, sessionId, role) {
            try {
                const priv = getIdentityPrivKey?.();
                const did = getClientDid?.();
                if (!priv || !did || !peerPc?.localDescription?.sdp) return {};
                const fingerprint = extractFingerprint(peerPc.localDescription.sdp);
                if (!fingerprint) return {};
                const fp_sig = await signFingerprint({ fingerprint, sessionId: sessionId || '', role, privKey: priv });
                return { fp_sig, fp_signer: did };
            } catch { return {}; }
        }

        // Verify a group co-member's SDP against their known identity, if we have one.
        // Refuse on a proven mismatch; allow (unverified) when the identity is unknown.
        async function _verifyGroupSdp(peerWebid, sdp, role, event) {
            const expectedDid = getExpectedPeerDid?.(null, { caller_webid: peerWebid, from_webid: peerWebid }) || '';
            const verdict = await classifyPeerSdp({
                sdp,
                role,
                signatureB64: event?.fp_sig || '',
                signerDid: event?.fp_signer || '',
                expectedDid,
            });
            // Advisory (see _verifyPeerSdp): never refuse; track state for the tile badge.
            state._verifyState = verdict === 'verified' ? 'verified' : 'unverified';
            return true;
        }

        // Persistent per-peer video tile (created on first track, removed on leave).
        function _renderPeerVideo(webid, stream) {
            const grid = document.getElementById('voice-channel-videos');
            if (!grid) return;
            let el = state.peerVideoElements[webid];
            if (!el) {
                el = document.createElement('video');
                el.autoplay = true; el.playsInline = true;
                el.className = 'vc-video-tile';
                el.dataset.vcWebid = webid;
                el.style.cssText = 'width:160px;max-height:120px;background:#000;border-radius:6px;object-fit:cover;';
                state.peerVideoElements[webid] = el;
                grid.appendChild(el);
            }
            el.srcObject = stream || null;
            el.play?.().catch(() => {});
            grid.style.display = Object.keys(state.peerVideoElements).length ? 'flex' : 'none';
        }

        function _addChannelParticipant(webid) {
            state._channelParticipants[webid] = { name: webid.slice(-12), state: "connecting" };
            _showChannelPanel();
            _renderChannelPanel();
        }

        // Speaking detection (Phase J): one shared AudioContext + an AnalyserNode per
        // remote stream; a single throttled rAF loop samples levels and toggles the
        // .vc-speaking ring on each participant pill directly (no full re-render).
        const _speaking = (() => {
            let ctx = null, raf = null, lastTick = 0;
            const nodes = {};  // webid -> { source, analyser, data }
            const THRESHOLD = 0.045;
            function setSpeaking(webid, on) {
                const pill = document.querySelector?.(
                    '#voice-channel-participants [data-vc-webid="' + webid + '"]');
                if (pill) pill.classList.toggle('vc-speaking', on);
                // R81 Q3: also ring the participant's video tile (group) so the active
                // speaker is visible, not just their pill.
                const tile = document.querySelector?.(
                    '#voice-channel-videos [data-vc-webid="' + webid + '"]');
                if (tile) tile.classList.toggle('vc-speaking', on);
            }
            function loop() {
                if (raf) return;
                const tick = (ts) => {
                    if (Object.keys(nodes).length === 0) { raf = null; return; }
                    raf = requestAnimationFrame(tick);
                    if (ts - lastTick < 80) return;  // ~12 Hz
                    lastTick = ts;
                    for (const webid of Object.keys(nodes)) {
                        const n = nodes[webid];
                        n.analyser.getByteTimeDomainData(n.data);
                        setSpeaking(webid, audioLevel(n.data) > THRESHOLD);
                    }
                };
                raf = requestAnimationFrame(tick);
            }
            return {
                attach(webid, stream) {
                    try {
                        const AC = window.AudioContext || window.webkitAudioContext;
                        if (!AC || !stream) return;
                        if (!ctx) ctx = new AC();
                        this.detach(webid);
                        const source = ctx.createMediaStreamSource(stream);
                        const analyser = ctx.createAnalyser();
                        analyser.fftSize = 256;
                        source.connect(analyser);
                        nodes[webid] = { source, analyser, data: new Uint8Array(analyser.fftSize) };
                        loop();
                    } catch (_) {}
                },
                detach(webid) {
                    const n = nodes[webid];
                    if (n) { try { n.source.disconnect(); } catch (_) {} delete nodes[webid]; }
                    setSpeaking(webid, false);
                    if (Object.keys(nodes).length === 0) this.stopAll();
                },
                stopAll() {
                    for (const w of Object.keys(nodes)) {
                        try { nodes[w].source.disconnect(); } catch (_) {}
                        delete nodes[w];
                    }
                    if (raf) { cancelAnimationFrame(raf); raf = null; }
                    if (ctx) { try { ctx.close(); } catch (_) {} ctx = null; }
                },
            };
        })();

        function _removeChannelParticipant(webid) {
            delete state._channelParticipants[webid];
            if (Object.keys(state._channelParticipants).length === 0 && !state._inVoiceChannel) {
                _hideChannelPanel();
            } else {
                _renderChannelPanel();
            }
        }

        function _updateChannelParticipantUI(webid, connState) {
            // NB: param was named `state`, shadowing the voice-state cluster — so
            // `state._channelParticipants` read off the connState STRING and the
            // participant connection-status dot (green/amber/red) never updated,
            // making a failed/dropped peer connection invisible. Use connState.
            if (state._channelParticipants[webid]) {
                state._channelParticipants[webid].state = connState;
                _renderChannelPanel();
            }
        }

        function _renderChannelPanel() {
            const container = document.getElementById("voice-channel-participants");
            if (!container) return;
            const stateColor = { connected: "#4ade80", connecting: "#fbbf24",
                                  checking: "#fbbf24", completed: "#4ade80",
                                  disconnected: "#f87171", failed: "#f87171", closed: "#64748b" };
            container.innerHTML = Object.entries(state._channelParticipants).map(([webid, info]) => {
                const color = stateColor[info.state] || "#94a3b8";
                return `<span data-vc-webid="${escHtml(webid)}" style="background:#1e293b;padding:3px 8px;border-radius:12px;font-size:0.78em;color:#f1f5f9;display:flex;align-items:center;gap:4px;">
                    <span style="width:6px;height:6px;border-radius:50%;background:${color};display:inline-block;"></span>
                    ${escHtml(info.name)}
                </span>`;
            }).join("");
        }

        function _showChannelPanel() {
            const p = document.getElementById("voice-channel-panel");
            if (p) p.style.display = "flex";
        }

        function _hideChannelPanel() {
            const p = document.getElementById("voice-channel-panel");
            if (p) p.style.display = "none";
            Object.keys(state._channelParticipants).forEach(k => delete state._channelParticipants[k]);
        }

        async function initWebRTC(certId, sessionId, isCaller = false, sdpOffer = null, withVideo = false) {
            const iceServers = await _getIceServers();
            state.pc = new RTCPeerConnection({ iceServers: iceServers });
            state._pendingCandidates = [];
            state._remoteDescSet = false;
            state.currentCallSessionId = sessionId;
            state._verifyState = 'unverified';
            // The peer's routable webid, so answer + ICE reach them across gateways
            // (the 1:1 session model only routes within one gateway). Caller: the DM
            // peer; callee: whoever invited us.
            state._callPeerWebid = isCaller
                ? (getActiveView() ? getActiveView().peerWebid : null)
                : ((state.currentCall && state.currentCall.caller_webid)
                    || (getActiveView() ? getActiveView().peerWebid : null));

            const stream = await getMedia();
            // A caller with no microphone would start a call the other side can't
            // hear — abort cleanly instead of silently establishing a dead call.
            if (isCaller && !stream) {
                showToast(t('voice.micRequired'), "error");
                hangupCleanup();
                return;
            }
            // Media is attached PER ROLE below: the caller adds its audio track and a
            // video transceiver before the offer; the callee sets the remote offer
            // FIRST, then attaches to the offer's own transceivers. Attaching before
            // setRemoteDescription mis-associates m-lines and produces a recvonly
            // answer (the callee's media never reaches the caller).
            state.videoEnabled = false;
            state._cameraTrack = null;
            state.remoteStream = null;

            state.pc.ontrack = (event) => {
                // Build the remote stream from individual tracks: a track put in via
                // replaceTrack (camera, screen) carries NO MediaStream, so event.streams
                // is empty for it. Accumulating event.track ourselves is the robust path.
                if (!state.remoteStream) state.remoteStream = new MediaStream();
                try { state.remoteStream.addTrack(event.track); } catch (_) { /* dup */ }
                _renderVideo('vw-remote-video', state.remoteStream);
                _syncRemoteVideoVisibility();
                if (event.track) event.track.onmute = event.track.onunmute = event.track.onended = _syncRemoteVideoVisibility;
                setCallState(CallState.CONNECTED);
                _updateVerifyBadge();
                const peerName = getActiveView() ? (getActiveView().name || getActiveView().id || "") : "";
                const pn = document.getElementById("vw-peer-name");
                if (pn) pn.textContent = peerName || "";
            };

            state.pc.onicecandidate = (e) => {
                if (e.candidate) {
                    if (state._remoteDescSet) {
                        getSocket().send(JSON.stringify({
                            cmd: "ice_candidate",
                            cert_id: certId,
                            session_id: sessionId,
                            // Route by webid so ICE reaches a peer on another gateway.
                            target_webid: state._callPeerWebid || undefined,
                            candidate: e.candidate.candidate,
                            sdp_mid: e.candidate.sdpMid,
                            sdp_mline_index: e.candidate.sdpMLineIndex
                        }));
                    } else {
                        state._pendingCandidates.push(e.candidate);
                    }
                }
            };

            // A 1:1 call previously had no connection monitor: if ICE failed after
            // the call connected (network change, NAT rebind, peer crash), the timer
            // kept ticking over dead audio and the user was never told. "failed" is
            // the reliable signal — unlike the often-transient "disconnected" — that
            // it will not self-recover, so surface it and end the call cleanly.
            // _doHangup also signals the peer, so both sides tear down.
            state.pc.oniceconnectionstatechange = () => {
                if (state.pc && state.pc.iceConnectionState === "failed") {
                    showToast(t('voice.connectionLost'), "error");
                    _doHangup();
                }
            };

            if (isCaller) {
                // Add our audio and a sendrecv video m-line, then (optionally) the
                // camera, then offer.
                if (stream) stream.getTracks().forEach(track => state.pc.addTrack(track, stream));
                const vt = state.pc.addTransceiver('video', { direction: 'sendrecv' });
                state._videoSender = vt.sender;
                _capSender(vt.sender);
                if (withVideo) { try { await toggleCamera(); } catch (_) {} }
                const offer = await state.pc.createOffer();
                await state.pc.setLocalDescription(offer);
                state.currentCallSessionId = sessionId;
                setCallState(CallState.CALLING);
                _startCallTimeout();
                const fp = await _signLocalFingerprint('offer');
                getSocket().send(JSON.stringify({
                    cmd: "voice_invite",
                    cert_id: certId,
                    session_id: sessionId,
                    target_webid: getActiveView() ? getActiveView().peerWebid : null,
                    sdp_offer: offer.sdp,
                    ...fp,
                }));
            } else if (sdpOffer) {
                // Authenticate the media channel (advisory; see _verifyPeerSdp).
                await _verifyPeerSdp(sdpOffer, 'offer', state.currentCall || {});
                // Set the remote offer FIRST, then attach our media to the offer's OWN
                // transceivers via replaceTrack (addTrack can spawn a new m-line the
                // caller never offered, leaving that transceiver recvonly so our media
                // never reaches the caller). Force each to sendrecv.
                await _setRemoteAndDrainCandidates(sdpOffer, 'offer');
                const txs = state.pc.getTransceivers();
                const audioTx = txs.find(tt => tt.receiver && tt.receiver.track && tt.receiver.track.kind === 'audio');
                const videoTx = txs.find(tt => tt.receiver && tt.receiver.track && tt.receiver.track.kind === 'video');
                const audioTrack = stream ? stream.getAudioTracks()[0] : null;
                if (audioTx) {
                    try { audioTx.direction = 'sendrecv'; } catch (_) { /* older browsers */ }
                    if (audioTrack) { try { await audioTx.sender.replaceTrack(audioTrack); } catch (_) {} }
                } else if (stream) {
                    stream.getTracks().forEach(track => state.pc.addTrack(track, stream));
                }
                // Adopt the offer's video transceiver as our sender, sendrecv, so the
                // camera can be turned on later without renegotiation.
                if (videoTx) {
                    try { videoTx.direction = 'sendrecv'; } catch (_) { /* older browsers */ }
                    state._videoSender = videoTx.sender;
                    _capSender(videoTx.sender);
                }
                const answer = await state.pc.createAnswer();
                await state.pc.setLocalDescription(answer);
                state.currentCallSessionId = sessionId;
                const fp = await _signLocalFingerprint('answer');
                getSocket().send(JSON.stringify({
                    cmd: "voice_answer",
                    cert_id: certId,
                    session_id: sessionId,
                    // Route by webid so the answer reaches a caller on another gateway.
                    target_webid: state._callPeerWebid || undefined,
                    sdp_answer: answer.sdp,
                    ...fp,
                }));
                setCallState(CallState.CONNECTED);
                startCallTimer();
            }
        }

        function startCallTimer() {
            _startStatsMonitor();
            state.callStartTime = Date.now();
            if (state.callTimerInterval) clearInterval(state.callTimerInterval);
            state.callTimerInterval = setInterval(() => {
                const s = Math.floor((Date.now() - state.callStartTime) / 1000);
                const mm = String(Math.floor(s / 60)).padStart(2, "0");
                const ss = String(s % 60).padStart(2, "0");
                const statusEl = document.getElementById("vw-status");
                if (statusEl) statusEl.textContent = t('voice.inCallTimer', { time: `${mm}:${ss}` });
            }, 1000);
        }

        function stopCallTimer() {
            if (state.callTimerInterval) { clearInterval(state.callTimerInterval); state.callTimerInterval = null; }
            state.callStartTime = null;
        }

        function stopRingTone() {
            if (state.ringOscillator) { try { state.ringOscillator.stop(); } catch(e) {} state.ringOscillator = null; }
        }

        function playRingTone() {
            stopRingTone();
            try {
                const ctx = new AudioContext();
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.connect(gain); gain.connect(ctx.destination);
                osc.frequency.value = 440; gain.gain.value = 0.08;
                osc.start();
                state.ringOscillator = osc;
                setTimeout(() => stopRingTone(), 30000); // auto-stop after 30s
            } catch(e) { console.warn("Ring tone failed", e); }
        }

        function hangupCleanup() {
            if (state.pc) { state.pc.close(); state.pc = null; }
            if (state._remoteAudio) { try { state._remoteAudio.pause(); state._remoteAudio.srcObject = null; } catch (_) {} state._remoteAudio = null; }
            if (state.localStream) { state.localStream.getTracks().forEach(tr => tr.stop()); state.localStream = null; }
            // Release the camera and clear the video surfaces so the OS capture
            // indicator goes out and no self-view lingers after the call.
            if (state._cameraTrack) { try { state._cameraTrack.stop(); } catch (_) {} state._cameraTrack = null; }
            if (getIsSharing && getIsSharing()) { try { stopScreenShare(); } catch (_) {} }
            state.videoEnabled = false;
            state._videoSender = null;
            state.remoteStream = null;
            state._verifyState = 'unverified';
            _renderVideo('vw-local-video', null);
            _renderVideo('vw-remote-video', null);
            state._mediaDenied = false;
            _stopStatsMonitor();
            stopCallTimer();
            stopRingTone();
            _clearCallTimeout();
            state._pendingCandidates = [];
            state._remoteDescSet = false;
            state.isMuted = false;
            state.currentCallSessionId = null;
            setCallState(CallState.IDLE);
            const muteBtn = document.getElementById("mute-btn");
            if (muteBtn) muteBtn.classList.remove("vw-muted");
            const vwPeer = document.getElementById("vw-peer-name");
            if (vwPeer) vwPeer.textContent = "";
            const vwStatus = document.getElementById("vw-status");
            if (vwStatus) vwStatus.textContent = t('voice.inCall');
        }

        function handleVoiceHangup(event) {
            if (state._callState !== CallState.IDLE) setCallState(CallState.ENDING);
            hangupCleanup();
        }

        function handleVoiceSignalRelay(event) {
            const st = event.signal_type;
            const sd = event.signal_data || {};
            const merged = { session_id: event.session_id, from_webid: event.from_webid, ...sd };
            const isGroupPeer = event.from_webid && state.peerConnections[event.from_webid];
            if (st === "answer") {
                isGroupPeer ? handleGroupVoiceAnswer(merged) : handleVoiceAnswer(merged);
            } else if (st === "ice_candidate") {
                isGroupPeer ? handleGroupIceCandidate(merged) : handleIceCandidate(merged);
            } else if (st === "hangup") {
                handleVoiceHangup(merged);
            } else if (st === "offer") {
                // Cross-gateway group channel offer: auto-answer if we're in a channel
                if (state._inVoiceChannel && event.from_webid) {
                    _addChannelParticipant(event.from_webid);
                    initWebRTCForPeer(event.from_webid, event.session_id, false, sd.sdp_offer,
                        { session_id: event.session_id, fp_sig: sd.fp_sig, fp_signer: sd.fp_signer })
                        .catch(console.warn);
                } else {
                    showVoiceBanner({ ...merged, caller_webid: event.from_webid, sdp_offer: sd.sdp_offer });
                }
            }
        }

        function handleVoicePeerPresent(event) {
            showToast(t('voice.peerPresent', { peer: event.peer_webid.slice(0, 20) }), "info");
            _addChannelParticipant(event.peer_webid);
        }

        function handleVoicePeerJoined(event) {
            showToast(t('voice.peerJoined', { peer: event.peer_webid.slice(0, 20) }), "info");
            _addChannelParticipant(event.peer_webid);
            // We are an existing member; call the new joiner (one offer per pair).
            initWebRTCForPeer(event.peer_webid, null, true).catch(console.warn);
            recapSenders();   // the mesh grew: re-divide each sender's bitrate budget
        }

        function handleVoicePeerLeft(event) {
            showToast(t('voice.peerLeft', { peer: event.peer_webid.slice(0, 20) }), "info");
            const peerPc = state.peerConnections[event.peer_webid];
            if (peerPc) { try { peerPc.close(); } catch (_) {} delete state.peerConnections[event.peer_webid]; }
            const audio = state.peerAudioElements[event.peer_webid];
            if (audio) { audio.srcObject = null; delete state.peerAudioElements[event.peer_webid]; }
            const vtile = state.peerVideoElements[event.peer_webid];
            if (vtile) { try { vtile.srcObject = null; vtile.remove(); } catch (_) {} delete state.peerVideoElements[event.peer_webid]; }
            delete state._peerVideoSenders[event.peer_webid];
            delete state._channelSessionIds[event.peer_webid];
            _speaking.detach(event.peer_webid);
            _removeChannelParticipant(event.peer_webid);
            recapSenders();   // the mesh shrank: senders can reclaim bitrate
        }

        async function handleVoiceAnswer(event) {
            if (state.pc) {
                // Authenticate the answerer's media channel before accepting it.
                if (!(await _verifyPeerSdp(event.sdp_answer, 'answer', event))) {
                    _doHangup();
                    return;
                }
                await _setRemoteAndDrainCandidates(event.sdp_answer, 'answer');
                _clearCallTimeout();
                setCallState(CallState.CONNECTED);
                startCallTimer();
            }
        }

        async function handleIceCandidate(event) {
            if (state.pc) {
                try {
                    await state.pc.addIceCandidate({
                        candidate: event.candidate,
                        sdpMid: event.sdp_mid,
                        sdpMLineIndex: event.sdp_mline_index
                    });
                } catch (e) { console.warn("ICE error", e); }
            }
        }

        async function handleGroupVoiceAnswer(event) {
            const peerPc = state.peerConnections[event.from_webid];
            if (!peerPc) return;
            // Authenticate the answerer's media channel where identity is known.
            if (!(await _verifyGroupSdp(event.from_webid, event.sdp_answer, 'answer', event))) {
                try { peerPc.close(); } catch (_) {}
                delete state.peerConnections[event.from_webid];
                _removeChannelParticipant(event.from_webid);
                return;
            }
            try {
                await peerPc.setRemoteDescription({ type: "answer", sdp: event.sdp_answer });
                _updateChannelParticipantUI(event.from_webid, "connected");
            } catch (e) { console.warn("group answer error", e); }
        }

        async function handleGroupIceCandidate(event) {
            const peerPc = state.peerConnections[event.from_webid];
            if (!peerPc) return;
            try {
                await peerPc.addIceCandidate({
                    candidate: event.candidate,
                    sdpMid: event.sdp_mid,
                    sdpMLineIndex: event.sdp_mline_index,
                });
            } catch (e) { console.warn("group ICE error", e); }
        }

        function _doHangup() {
            setCallState(CallState.ENDING);
            if (state.currentCallSessionId && getSocket() && getSocket().readyState === WebSocket.OPEN) {
                getSocket().send(JSON.stringify({cmd: "voice_hangup", session_id: state.currentCallSessionId}));
            }
            hangupCleanup();
        }

    return {
        state,
        initWebRTC,
        initWebRTCForPeer,
        _getIceServers,
        getMedia,
        toggleCamera,
        recapSenders,
        getQualityProfile,
        setQualityProfile,
        getTurnCredentials,
        handleVoiceAnswer,
        handleIceCandidate,
        handleGroupVoiceAnswer,
        handleGroupIceCandidate,
        handleVoicePeerJoined,
        handleVoicePeerPresent,
        handleVoicePeerLeft,
        handleVoiceSignalRelay,
        showVoiceBanner,
        handleVoiceUnavailable,
        handleVoiceHangup,
        hangupCleanup,
        joinVoice,
        leaveVoiceChannel,
        _addChannelParticipant,
        _removeChannelParticipant,
        _updateChannelParticipantUI,
        _renderChannelPanel,
        _showChannelPanel,
        _hideChannelPanel,
        setCallState,
        startCallTimer,
        stopCallTimer,
        _startCallTimeout,
        _clearCallTimeout,
        playRingTone,
        stopRingTone,
        _callerDisplayName,
        _setRemoteAndDrainCandidates,
        updateVoiceChannels,
        _doHangup,
        _updateCallUI,
    };
}
