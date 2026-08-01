// Voice + WebRTC subsystem (1:1 calls + group voice channels), extracted from
// main.js (R40). createVoice(deps) owns voice state in `state` and returns the
// handlers main.js wires into the WS dispatch and call/mute/leave buttons.
import { t } from './i18n.js';
import { escHtml } from './util.js';
import { extractFingerprint, signFingerprint, classifyPeerSdp } from './callsec.js';

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
    };

        function updateVoiceChannels(roomId) {
            // Voice channels not yet implemented — keep section hidden
        }

        async function joinVoice(roomId) {
            getSocket().send(JSON.stringify({cmd: "join_voice_channel", room_id: roomId}));
            state._inVoiceChannel = roomId;
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
            state._channelSessionIds = {};
            // Release the microphone — otherwise the OS/browser recording
            // indicator stays lit after leaving the channel.
            if (state.localStream) { state.localStream.getTracks().forEach(tr => tr.stop()); state.localStream = null; }
            state._mediaDenied = false;
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
            setTimeout(() => {
                if (state._callState === CallState.RINGING) {
                    banner.style.display = "none";
                    state.currentCall = null;
                    setCallState(CallState.IDLE);
                }
            }, 30000);
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
                state.localStream = await navigator.mediaDevices.getUserMedia({
                    audio: { noiseSuppression: true, echoCancellation: true, autoGainControl: true },
                    video: false,
                });
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
            const verdict = await classifyPeerSdp({
                sdp,
                sessionId: state.currentCallSessionId || event?.session_id || '',
                role,
                signatureB64: event?.fp_sig || '',
                signerDid: event?.fp_signer || '',
                expectedDid: getExpectedPeerDid?.(getActiveView(), event) || '',
            });
            if (verdict === 'mismatch') {
                showToast(t('voice.identityUnverified'), 'error');
                state._verifyState = 'mismatch';
                return false;
            }
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
        async function toggleCamera() {
            if (!state.pc || !state._videoSender) return state.videoEnabled;
            if (state.videoEnabled) {
                try { state._cameraTrack?.stop(); } catch (_) {}
                state._cameraTrack = null;
                await state._videoSender.replaceTrack(null);
                state.videoEnabled = false;
                _renderVideo('vw-local-video', null);
            } else {
                let cam;
                try { cam = await navigator.mediaDevices.getUserMedia({ video: true, audio: false }); }
                catch (err) { showToast(t('voice.cameraError', { error: (err && err.name) || err }), 'error'); return false; }
                const track = cam.getVideoTracks()[0];
                if (!track) return false;
                state._cameraTrack = track;
                await state._videoSender.replaceTrack(track);
                state.videoEnabled = true;
                _renderVideo('vw-local-video', cam, { mirror: true, muted: true });
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

        async function initWebRTCForPeer(targetWebid, sessionId, isCaller = false, sdpOffer = null) {
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

            peerPc.ontrack = (event) => {
                let audio = state.peerAudioElements[targetWebid];
                if (!audio) {
                    audio = new Audio();
                    audio.autoplay = true;
                    state.peerAudioElements[targetWebid] = audio;
                }
                audio.srcObject = event.streams[0];
                audio.play().catch(() => {});
                _updateChannelParticipantUI(targetWebid, "connected");
                _speaking.attach(targetWebid, event.streams[0]);
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
                getSocket()?.send(JSON.stringify({
                    cmd: "voice_invite",
                    target_webid: targetWebid,
                    sdp_offer: offer.sdp,
                    channel_id: state._inVoiceChannel || "",
                }));
            } else if (sdpOffer) {
                await peerPc.setRemoteDescription({ type: "offer", sdp: sdpOffer });
                const answer = await peerPc.createAnswer();
                await peerPc.setLocalDescription(answer);
                getSocket()?.send(JSON.stringify({
                    cmd: "voice_answer",
                    target_webid: targetWebid,
                    session_id: sessionId,
                    sdp_answer: answer.sdp,
                }));
            }
            return peerPc;
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
                const el = document.querySelector?.(
                    '#voice-channel-participants [data-vc-webid="' + webid + '"]');
                if (el) el.classList.toggle('vc-speaking', on);
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

            const stream = await getMedia();
            // A caller with no microphone would start a call the other side can't
            // hear — abort cleanly instead of silently establishing a dead call.
            if (isCaller && !stream) {
                showToast(t('voice.micRequired'), "error");
                hangupCleanup();
                return;
            }
            if (stream) {
                stream.getTracks().forEach(track => state.pc.addTrack(track, stream));
            }
            // Negotiate a video m-line up front (sendrecv) on EVERY call, even
            // voice-only, so camera and screen share can be toggled later via
            // replaceTrack with no mid-call renegotiation.
            const vt = state.pc.addTransceiver('video', { direction: 'sendrecv' });
            state._videoSender = vt.sender;
            state.videoEnabled = false;
            state._cameraTrack = null;

            state.pc.ontrack = (event) => {
                // A <video> element plays both audio and video, so route the remote
                // stream there for voice and video calls alike.
                state.remoteStream = event.streams[0];
                _renderVideo('vw-remote-video', event.streams[0]);
                _syncRemoteVideoVisibility();
                event.streams[0].getVideoTracks().forEach(tr => {
                    tr.onmute = tr.onunmute = tr.onended = _syncRemoteVideoVisibility;
                });
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
                // Start-as-video: put the camera track into the video sender before
                // the offer so it is negotiated from the first exchange.
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
                // Refuse a call whose media channel we cannot authenticate to the
                // expected contact (a gateway MitM would show up here). The offer's
                // signature travelled in the invite we stored as state.currentCall.
                if (!(await _verifyPeerSdp(sdpOffer, 'offer', state.currentCall || {}))) {
                    hangupCleanup();
                    return;
                }
                await _setRemoteAndDrainCandidates(sdpOffer, 'offer');
                const answer = await state.pc.createAnswer();
                await state.pc.setLocalDescription(answer);
                state.currentCallSessionId = sessionId;
                const fp = await _signLocalFingerprint('answer');
                getSocket().send(JSON.stringify({
                    cmd: "voice_answer",
                    cert_id: certId,
                    session_id: sessionId,
                    sdp_answer: answer.sdp,
                    ...fp,
                }));
                setCallState(CallState.CONNECTED);
                startCallTimer();
            }
        }

        function startCallTimer() {
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
                    initWebRTCForPeer(event.from_webid, event.session_id, false, sd.sdp_offer)
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
        }

        function handleVoicePeerLeft(event) {
            showToast(t('voice.peerLeft', { peer: event.peer_webid.slice(0, 20) }), "info");
            const peerPc = state.peerConnections[event.peer_webid];
            if (peerPc) { try { peerPc.close(); } catch (_) {} delete state.peerConnections[event.peer_webid]; }
            const audio = state.peerAudioElements[event.peer_webid];
            if (audio) { audio.srcObject = null; delete state.peerAudioElements[event.peer_webid]; }
            delete state._channelSessionIds[event.peer_webid];
            _speaking.detach(event.peer_webid);
            _removeChannelParticipant(event.peer_webid);
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
