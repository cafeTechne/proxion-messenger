// Voice + WebRTC subsystem (1:1 calls + group voice channels), extracted from
// main.js (R40). createVoice(deps) owns voice state in `state` and returns the
// handlers main.js wires into the WS dispatch and call/mute/leave buttons.
import { escHtml } from './util.js';

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
    const { showToast, renderMessage, showOsNotification, sendCmd, playNotificationSound, normalizeRelayThreadId, stopScreenShare, getSocket, getActiveView, getSelfWebId, getTurnUrl, getTurnSecret, getLocalDmPeers, getCurrentRoomMembers, getIsSharing } = deps;
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
            if (state.localStream) { state.localStream.getTracks().forEach(t => t.stop()); state.localStream = null; }
            state._mediaDenied = false;
            _speaking.stopAll();
            _hideChannelPanel();
            showToast("Left voice channel");
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
                // ignores unknown constraints.
                state.localStream = await navigator.mediaDevices.getUserMedia({
                    audio: { noiseSuppression: true, echoCancellation: true, autoGainControl: true },
                    video: false,
                });
            } catch (err) {
                state._mediaDenied = true;
                showToast("Could not access microphone: " + (err && err.name ? err.name : err), "error");
            }
            return state.localStream;
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
            if (!connected && getIsSharing()) stopScreenShare();
            const statusEl = document.getElementById("vw-status");
            if (statusEl && !connected) {
                statusEl.textContent = state._callState === CallState.CALLING ? "Calling..." : "Incoming...";
            }
        }

        function _startCallTimeout() {
            if (state._callTimeoutId) clearTimeout(state._callTimeoutId);
            state._callTimeoutId = setTimeout(() => {
                if (state._callState === CallState.CALLING) {
                    showToast("Call not answered");
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
            if (stream) stream.getTracks().forEach(t => peerPc.addTrack(t, stream));

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
                    // doesn't spam. restartIce attempts automatic recovery.
                    if (!peerPc._proxionFailToasted) {
                        peerPc._proxionFailToasted = true;
                        showToast("Voice connection trouble with " + targetWebid.slice(0, 20) + " — reconnecting…", "error");
                    }
                    try { peerPc.restartIce(); } catch (_) {}
                } else if (_st === "connected" || _st === "completed") {
                    peerPc._proxionFailToasted = false;
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

        async function initWebRTC(certId, sessionId, isCaller = false, sdpOffer = null) {
            const iceServers = await _getIceServers();
            state.pc = new RTCPeerConnection({ iceServers: iceServers });
            state._pendingCandidates = [];
            state._remoteDescSet = false;
            
            const stream = await getMedia();
            // A caller with no microphone would start a call the other side can't
            // hear — abort cleanly instead of silently establishing a dead call.
            if (isCaller && !stream) {
                showToast("Microphone is required to start a call.", "error");
                hangupCleanup();
                return;
            }
            if (stream) {
                stream.getTracks().forEach(track => state.pc.addTrack(track, stream));
            }

            state.pc.ontrack = (event) => {
                console.log("Remote track received");
                const remoteAudio = new Audio();
                remoteAudio.srcObject = event.streams[0];
                remoteAudio.play().catch(() => {});
                // Keep a reference so the element isn't garbage-collected mid-call.
                state._remoteAudio = remoteAudio;
                setCallState(CallState.CONNECTED);
                const peerName = getActiveView() ? (getActiveView().name || getActiveView().id || "") : "";
                document.getElementById("vw-peer-name").textContent = peerName || "";
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

            if (isCaller) {
                const offer = await state.pc.createOffer();
                await state.pc.setLocalDescription(offer);
                state.currentCallSessionId = sessionId;
                setCallState(CallState.CALLING);
                _startCallTimeout();
                getSocket().send(JSON.stringify({
                    cmd: "voice_invite",
                    cert_id: certId,
                    session_id: sessionId,
                    target_webid: getActiveView() ? getActiveView().peerWebid : null,
                    sdp_offer: offer.sdp
                }));
            } else if (sdpOffer) {
                await _setRemoteAndDrainCandidates(sdpOffer, 'offer');
                const answer = await state.pc.createAnswer();
                await state.pc.setLocalDescription(answer);
                state.currentCallSessionId = sessionId;
                getSocket().send(JSON.stringify({
                    cmd: "voice_answer",
                    cert_id: certId,
                    session_id: sessionId,
                    sdp_answer: answer.sdp
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
                if (statusEl) statusEl.textContent = `In Call ${mm}:${ss}`;
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
            if (state.localStream) { state.localStream.getTracks().forEach(t => t.stop()); state.localStream = null; }
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
            if (vwStatus) vwStatus.textContent = "In Call";
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
            showToast(`${event.peer_webid.slice(0, 20)} is in the voice channel`, "info");
            _addChannelParticipant(event.peer_webid);
        }

        function handleVoicePeerJoined(event) {
            showToast(`${event.peer_webid.slice(0, 20)} joined the voice channel`, "info");
            _addChannelParticipant(event.peer_webid);
            // We are an existing member; call the new joiner (one offer per pair).
            initWebRTCForPeer(event.peer_webid, null, true).catch(console.warn);
        }

        function handleVoicePeerLeft(event) {
            showToast(`${event.peer_webid.slice(0, 20)} left the voice channel`, "info");
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
