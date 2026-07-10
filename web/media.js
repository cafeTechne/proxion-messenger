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

export function createMedia({ getSocket, getActiveView, showToast, getVoiceState }) {
    const state = {
        mediaRecorder: null,
        recordingChunks: [],
        recordingTimerInterval: null,
        recordingSeconds: 0,
        screenStream: null,
        isSharing: false,
    };

    function startVoiceRecording() {
        navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
            state.recordingChunks = []; state.recordingSeconds = 0;
            state.mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
            state.mediaRecorder.ondataavailable = e => { if (e.data.size > 0) state.recordingChunks.push(e.data); };
            state.mediaRecorder.onstop = sendVoiceMessage;
            state.mediaRecorder.start();
            document.getElementById('voice-recording-bar')?.classList.add('active');
            document.getElementById('voice-record-btn')?.classList.add('recording');
            state.recordingTimerInterval = setInterval(() => {
                state.recordingSeconds++;
                const timerEl = document.getElementById('recording-timer');
                if (timerEl) timerEl.textContent = Math.floor(state.recordingSeconds / 60) + ':' + String(state.recordingSeconds % 60).padStart(2, '0');
                if (state.recordingSeconds >= 60) stopVoiceRecording(false);
            }, 1000);
        }).catch(() => showToast(t('media.micDenied'), 'error'));
    }

    function stopVoiceRecording(send = true) {
        clearInterval(state.recordingTimerInterval);
        document.getElementById('voice-recording-bar')?.classList.remove('active');
        document.getElementById('voice-record-btn')?.classList.remove('recording');
        if (state.mediaRecorder && state.mediaRecorder.state !== 'inactive') {
            state.mediaRecorder._sendOnStop = send;
            state.mediaRecorder.stop();
        }
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
                audio_b64: reader2.result.split(',')[1], duration_ms: state.recordingSeconds * 1000,
                message_id: voiceMsgId,
            }));
            if (activeView.type === 'local_room') {
                podUploadVoiceAudio(activeView.id, voiceMsgId, blob).catch(() => {});
            }
        };
        reader2.readAsDataURL(blob);
    }

    // -- Round 67: Screen sharing --
    async function startScreenShare() {
        const voiceState = getVoiceState();
        if (state.isSharing || !voiceState.pc) return;
        try {
            state.screenStream = await navigator.mediaDevices.getDisplayMedia({ video: { cursor: 'always' }, audio: false });
        } catch { showToast(t('media.screenShareCancelled')); return; }
        state.isSharing = true;
        const screenTrack = state.screenStream.getVideoTracks()[0];
        const sender = voiceState.pc.getSenders().find(s => s.track?.kind === 'video');
        if (sender) sender.replaceTrack(screenTrack);
        else voiceState.pc.addTrack(screenTrack, state.screenStream);
        screenTrack.onended = () => stopScreenShare();
        const sBtn = document.getElementById('screenshare-btn');
        if (sBtn) sBtn.classList.add('vw-sharing');
        const socket = getSocket();
        if (socket && voiceState.currentCall) socket.send(JSON.stringify({ cmd: 'screenshare_started', session_id: voiceState.currentCall.session_id || '' }));
    }

    function stopScreenShare() {
        state.isSharing = false;
        state.screenStream?.getTracks().forEach(tr => tr.stop());
        state.screenStream = null;
        const sBtn = document.getElementById('screenshare-btn');
        if (sBtn) sBtn.classList.remove('vw-sharing');
        const socket = getSocket();
        const voiceState = getVoiceState();
        if (socket && voiceState.currentCall) socket.send(JSON.stringify({ cmd: 'screenshare_stopped', session_id: voiceState.currentCall.session_id || '' }));
    }

    return { startVoiceRecording, stopVoiceRecording, sendVoiceMessage, startScreenShare, stopScreenShare, state };
}
