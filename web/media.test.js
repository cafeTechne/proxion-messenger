import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createMedia } from './media.js';

beforeEach(() => {
  global.document = {
    getElementById: () => ({ classList: { add() {}, remove() {} }, textContent: '' }),
  };
  global.Blob = class { constructor(parts, opts) { this.parts = parts; this.opts = opts; } };
  // crypto is a read-only global in node; redefine it for a deterministic id.
  Object.defineProperty(globalThis, 'crypto', { value: { randomUUID: () => 'uuid-1' }, configurable: true });
  // FileReader stub: synchronously delivers a data URL to onloadend.
  global.FileReader = class {
    readAsDataURL() { this.result = 'data:audio/webm;base64,QUJD'; this.onloadend(); }
  };
});

function make(over = {}) {
  const sent = [];
  const socket = { readyState: 1, send: (s) => sent.push(JSON.parse(s)) };
  const voiceState = over.voiceState ?? { pc: null, currentCall: null };
  const media = createMedia({
    getSocket: () => (over.socket === undefined ? socket : over.socket),
    getActiveView: () => (over.activeView === undefined ? { type: 'dm', id: 'cert-1' } : over.activeView),
    showToast: over.showToast ?? (() => {}),
    getVoiceState: () => voiceState,
  });
  return { media, sent, voiceState };
}

describe('sendVoiceMessage', () => {
  it('does nothing unless a recording is pending', () => {
    const { media, sent } = make();
    media.state.mediaRecorder = { _sendOnStop: false };
    media.sendVoiceMessage();
    expect(sent).toHaveLength(0);
  });
  it('sends send_voice_message with the recorded audio', () => {
    const { media, sent } = make({ activeView: { type: 'dm', id: 'cert-1' } });
    media.state.mediaRecorder = { _sendOnStop: true };
    media.state.recordingChunks = [];
    media.state.recordingSeconds = 3;
    media.sendVoiceMessage();
    expect(sent).toContainEqual({
      cmd: 'send_voice_message', thread_id: 'cert-1',
      audio_b64: 'QUJD', duration_ms: 3000, message_id: 'uuid-1',
    });
  });
});

describe('stopVoiceRecording', () => {
  it('marks send intent and stops an active recorder', () => {
    global.clearInterval = vi.fn();
    const { media } = make();
    const stop = vi.fn();
    media.state.mediaRecorder = { state: 'recording', stop };
    media.stopVoiceRecording(true);
    expect(media.state.mediaRecorder._sendOnStop).toBe(true);
    expect(stop).toHaveBeenCalled();
  });
});

describe('startScreenShare', () => {
  it('is a no-op when there is no active peer connection', async () => {
    const { media, sent } = make({ voiceState: { pc: null, currentCall: null } });
    await media.startScreenShare();
    expect(media.state.isSharing).toBe(false);
    expect(sent).toHaveLength(0);
  });
  it('adds the screen track and signals screenshare_started', async () => {
    const track = { kind: 'video', onended: null };
    const stream = { getVideoTracks: () => [track], getTracks: () => [track] };
    global.navigator = { mediaDevices: { getDisplayMedia: () => Promise.resolve(stream) } };
    const pc = { getSenders: () => [], addTrack: vi.fn() };
    const { media, sent } = make({ voiceState: { pc, currentCall: { session_id: 's1' } } });
    await media.startScreenShare();
    expect(media.state.isSharing).toBe(true);
    expect(pc.addTrack).toHaveBeenCalled();
    expect(sent).toContainEqual({ cmd: 'screenshare_started', session_id: 's1' });
  });
});

describe('stopScreenShare', () => {
  it('stops tracks, clears state, and signals screenshare_stopped', () => {
    const stop = vi.fn();
    const { media, sent } = make({ voiceState: { pc: {}, currentCall: { session_id: 's1' } } });
    media.state.isSharing = true;
    media.state.screenStream = { getTracks: () => [{ stop }] };
    media.stopScreenShare();
    expect(media.state.isSharing).toBe(false);
    expect(stop).toHaveBeenCalled();
    expect(media.state.screenStream).toBe(null);
    expect(sent).toContainEqual({ cmd: 'screenshare_stopped', session_id: 's1' });
  });
});
