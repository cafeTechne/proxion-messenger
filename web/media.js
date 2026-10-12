// media.js — voice-message recording and screen sharing (the two getUserMedia /
// getDisplayMedia capture paths).
//
// A factory. Reassignable host state (socket, activeView) is read live via
// getters; showToast is injected. Screen sharing needs the live WebRTC peer
// connection owned by voice.js, so the voice instance's state is injected
// through getVoiceState() — a deferred getter, which lets main.js create media
// before voice (voice's deps in turn reference media.stopScreenShare and
// media.state.isSharing). All cluster state lives in `state`, so the host can
// read media.state.isSharing for the screenshare toggle.
import { t } from './i18n.js';
import { podUploadVoiceAudio } from './pod.js';

export function createMedia({ getSocket, getActiveView, showToast, getVoiceState, getVoice = () => null }) {
    const state = {
        mediaRecorder: null,
        recordingChunks: [],
        recordingTimerInterval: null,
        recordingSeconds: 0,
        recordingStartedAt: 0,
        durationMs: 0,
        pending: null,
        pressAt: 0,
        screenStream: null,
        isSharing: false,
    };

    // The mic button works two ways: hold it (release sends) or tap it (starts
    // recording; tap again to send). pressMic/releaseMic tell them apart by how
    // long the press lasted. getUserMedia is async, so a release can arrive
    // before the recorder exists; `pending` carries that intent across.
    const TAP_MS = 400;
    const MIN_MS = 500;

    function _setRecordingUi(on) {
        document.getElementById('voice-recording-bar')?.classList.toggle('active', on);
        document.getElementById('voice-record-btn')?.classList.toggle('recording', on);
    }

    function startVoiceRecording() {
        if (state.pending || (state.mediaRecorder && state.mediaRecorder.state !== 'inactive')) return;
        const pending = state.pending = { stop: null };
        navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
            state.pending = null;
            if (pending.stop === false) { stream.getTracks().forEach(tr => tr.stop()); return; }
            state.recordingChunks = []; state.recordingSeconds = 0; state.durationMs = 0;
            const rec = state.mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
            rec.ondataavailable = e => { if (e.data.size > 0) state.recordingChunks.push(e.data); };
            rec.onstop = () => { stream.getTracks().forEach(tr => tr.stop()); sendVoiceMessage(); };
            rec.start();
            state.recordingStartedAt = Date.now();
            _setRecordingUi(true);
            state.recordingTimerInterval = setInterval(() => {
                state.recordingSeconds++;
                const timerEl = document.getElementById('recording-timer');
                if (timerEl) timerEl.textContent = Math.floor(state.recordingSeconds / 60) + ':' + String(state.recordingSeconds % 60).padStart(2, '0');
                if (state.recordingSeconds >= 60) stopVoiceRecording(true);
            }, 1000);
            if (pending.stop === true) stopVoiceRecording(true);
        }).catch(() => { state.pending = null; showToast(t('media.micDenied'), 'error'); });
    }

    function stopVoiceRecording(send = true) {
        if (state.pending) { state.pending.stop = send; return; }
        clearInterval(state.recordingTimerInterval);
        _setRecordingUi(false);
        const timerEl = document.getElementById('recording-timer');
        if (timerEl) timerEl.textContent = '0:00';
        if (state.mediaRecorder && state.mediaRecorder.state !== 'inactive') {
            state.durationMs = Math.min(60_000, Date.now() - (state.recordingStartedAt || Date.now()));
            const tooShort = send && state.durationMs < MIN_MS;
            if (tooShort) showToast(t('voiceMsg.tooShort'));
            state.mediaRecorder._sendOnStop = send && !tooShort;
            state.mediaRecorder.stop();
        }
    }

    function _recording() {
        return !!(state.pending || (state.mediaRecorder && state.mediaRecorder.state !== 'inactive'));
    }

    function pressMic() {
        if (_recording()) { state.pressAt = 0; stopVoiceRecording(true); return; }
        state.pressAt = Date.now();
        startVoiceRecording();
    }

    function releaseMic() {
        if (!state.pressAt) return;
        const held = Date.now() - state.pressAt;
        state.pressAt = 0;
        if (held >= TAP_MS) stopVoiceRecording(true);
    }

    function sendVoiceMessage() {
        const activeView = getActiveView();
        const socket = getSocket();
        if (!state.mediaRecorder || !state.mediaRecorder._sendOnStop || !activeView || !socket) return;
        const blob = new Blob(state.recordingChunks, { type: 'audio/webm' });
        const voiceMsgId = crypto.randomUUID ? crypto.randomUUID() : (Date.now().toString(36));
        const reader2 = new FileReader();
        reader2.onloadend = () => {
            socket.send(JSON.stringify({
                cmd: 'send_voice_message', thread_id: activeView.id,
                audio_b64: reader2.result.split(',')[1], duration_ms: state.durationMs,
                message_id: voiceMsgId,
            }));
            if (activeView.type === 'local_room') {
                podUploadVoiceAudio(activeView.id, voiceMsgId, blob).catch(() => {});
            }
        };
        reader2.readAsDataURL(blob);
    }

    // Every pre-negotiated video sender in the current call: the 1:1 sender plus each
    // group peer's, so screen share fans out to everyone with no renegotiation.
    function _videoSenders(voiceState) {
        const out = [];
        if (voiceState._videoSender) out.push(voiceState._videoSender);
        for (const s of Object.values(voiceState._peerVideoSenders || {})) if (s) out.push(s);
        // Fallback for a 1:1 call whose sender ref is not exposed: find it on the pc.
        if (!out.length && voiceState.pc) {
            const s = voiceState.pc.getSenders().find(x => x.track?.kind === 'video');
            if (s) out.push(s);
        }
        return out;
    }

    // -- Round 67: Screen sharing --
    async function startScreenShare() {
        const voiceState = getVoiceState();
        const senders = _videoSenders(voiceState);
        if (state.isSharing || !senders.length) return;
        try {
            state.screenStream = await navigator.mediaDevices.getDisplayMedia({ video: { cursor: 'always' }, audio: false });
        } catch { showToast(t('media.screenShareCancelled')); return; }
        state.isSharing = true;
        const screenTrack = state.screenStream.getVideoTracks()[0];
        for (const s of senders) { try { s.replaceTrack(screenTrack); } catch (_) {} }
        try { getVoice()?.recapSenders?.(); } catch (_) {}   // apply the screen-share profile
        screenTrack.onended = () => stopScreenShare();
        // Reflect the sharing state on both the 1:1 and the channel screenshare
        // buttons (aria-pressed + class), so "am I sharing?" is visible and exposed
        // to assistive tech in group calls too (was 1:1-class-only).
        for (const _id of ['screenshare-btn', 'voice-channel-screenshare-btn']) {
            const b = document.getElementById(_id);
            if (b) { b.classList.add('vw-sharing'); b.setAttribute('aria-pressed', 'true'); }
        }
        const socket = getSocket();
        if (socket && voiceState.currentCall) socket.send(JSON.stringify({ cmd: 'screenshare_started', session_id: voiceState.currentCall.session_id || '' }));
    }

    function stopScreenShare() {
        state.isSharing = false;
        state.screenStream?.getTracks().forEach(tr => tr.stop());
        state.screenStream = null;
        const voiceState = getVoiceState();
        // Restore every video sender to the camera track (if the camera is on) or to
        // nothing. No renegotiation, since the m-lines already exist.
        const restore = voiceState.videoEnabled ? (voiceState._cameraTrack || null) : null;
        for (const s of _videoSenders(voiceState)) { try { s.replaceTrack(restore); } catch (_) {} }
        try { getVoice()?.recapSenders?.(); } catch (_) {}   // back to the camera profile
        for (const _id of ['screenshare-btn', 'voice-channel-screenshare-btn']) {
            const b = document.getElementById(_id);
            if (b) { b.classList.remove('vw-sharing'); b.setAttribute('aria-pressed', 'false'); }
        }
        const socket = getSocket();
        if (socket && voiceState.currentCall) socket.send(JSON.stringify({ cmd: 'screenshare_stopped', session_id: voiceState.currentCall.session_id || '' }));
    }

    return { startVoiceRecording, stopVoiceRecording, pressMic, releaseMic, sendVoiceMessage, startScreenShare, stopScreenShare, state };
}
