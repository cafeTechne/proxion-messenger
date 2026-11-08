import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createMedia } from './media.js';

beforeEach(() => {
  global.document = {
    getElementById: () => ({ classList: { add() {}, remove() {}, toggle() {} }, textContent: '', setAttribute() {} }),
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
    getSessionId: over.getSessionId, canRecord: over.canRecord,
  });
  return { media, sent, voiceState };
}

describe('sendVoiceMessage', () => {
  it.each(['conversation', 'socket', 'account', 'capability'])('does not send after a delayed read and a changed %s', change => {
    const readers = [], showToast = vi.fn();
    global.FileReader = class { readAsDataURL() { readers.push(this); } };
    let session = 'one', allowed = true;
    const options = { activeView: { type: 'dm', id: 'first' }, showToast, getSessionId: () => session, canRecord: () => allowed };
    const { media, sent } = make(options);
    media.state.mediaRecorder = { _sendOnStop: true };
    media.state.durationMs = 3000;
    media.sendVoiceMessage();
    if (change === 'conversation') options.activeView.id = 'second';
    if (change === 'socket') options.socket = { readyState: 1, send: vi.fn() };
    if (change === 'account') session = 'two';
    if (change === 'capability') allowed = false;
    readers[0].result = 'data:audio/webm;base64,QUJD'; readers[0].onloadend();
    expect(sent).toHaveLength(0);
    expect(showToast).toHaveBeenCalledWith('media.conversationChanged', 'error');
  });
  it('keeps the stopped recording duration when another recording updates state', () => {
    let reader;
    global.FileReader = class { readAsDataURL() { reader = this; } };
    const { media, sent } = make();
    media.state.mediaRecorder = { _sendOnStop: true }; media.state.durationMs = 3000;
    media.sendVoiceMessage(); media.state.durationMs = 9000;
    reader.result = 'data:audio/webm;base64,QUJD'; reader.onloadend();
    expect(sent[0].duration_ms).toBe(3000);
  });
  it('reports a failed audio read instead of sending an empty message', () => {
    const showToast = vi.fn();
    global.FileReader = class { readAsDataURL() { this.result = null; this.onloadend(); } };
    const { media, sent } = make({ showToast });
    media.state.mediaRecorder = { _sendOnStop: true }; media.sendVoiceMessage();
    expect(sent).toHaveLength(0);
    expect(showToast).toHaveBeenCalledWith('voiceMsg.readFailed', 'error');
  });
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
    media.state.durationMs = 3000;
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
    media.state.recordingStartedAt = Date.now() - 2000;
    media.stopVoiceRecording(true);
    expect(media.state.mediaRecorder._sendOnStop).toBe(true);
    expect(stop).toHaveBeenCalled();
  });
});

describe('mic press and release', () => {
  // A fake recorder and a getUserMedia whose promise the test resolves.
  function rig() {
    let resolveMic;
    const track = { stop: vi.fn() };
    const stream = { getTracks: () => [track] };
    global.navigator = { mediaDevices: { getUserMedia: () => new Promise(r => { resolveMic = r; }) } };
    global.MediaRecorder = class {
      constructor() { this.state = 'inactive'; }
      start() { this.state = 'recording'; }
      stop() { this.state = 'inactive'; this.onstop?.(); }
    };
    global.setInterval = vi.fn(() => 1);
    global.clearInterval = vi.fn();
    return { track, grant: async () => { resolveMic(stream); await Promise.resolve(); await Promise.resolve(); } };
  }

  it('releases the microphone if the user changes conversation during the permission prompt', async () => {
    const { track, grant } = rig(), showToast = vi.fn();
    const options = { activeView: { type: 'dm', id: 'first' }, showToast };
    const { media, sent } = make(options);
    media.pressMic(); options.activeView = { type: 'dm', id: 'second' };
    await grant();
    expect(track.stop).toHaveBeenCalled();
    expect(media.state.mediaRecorder).toBeNull(); expect(sent).toHaveLength(0);
    expect(showToast).toHaveBeenCalledWith('media.conversationChanged', 'error');
  });
  it('does not move a stopped recording into a different conversation', async () => {
    const { track, grant } = rig();
    const options = { activeView: { type: 'dm', id: 'first' } };
    const { media, sent } = make(options);
    media.pressMic(); await grant(); media.state.recordingStartedAt = Date.now() - 2000;
    options.activeView = { type: 'dm', id: 'second' }; media.stopVoiceRecording(true);
    expect(track.stop).toHaveBeenCalled(); expect(sent).toHaveLength(0);
  });
  it('automatically releases an active microphone after the account changes', async () => {
    const { track, grant } = rig(); let session = 'first';
    const { media, sent } = make({ getSessionId: () => session });
    media.pressMic(); await grant(); session = 'second';
    global.setInterval.mock.calls[0][0]();
    expect(track.stop).toHaveBeenCalled(); expect(sent).toHaveLength(0);
    expect(media.state.mediaRecorder.state).toBe('inactive');
  });
  it('does not request a microphone for a conversation without voice-message support', () => {
    rig(); const request = vi.spyOn(navigator.mediaDevices, 'getUserMedia');
    const { media } = make({ canRecord: () => false }); media.pressMic();
    expect(request).not.toHaveBeenCalled();
  });
  it('releases an acquired microphone when recorder construction fails', async () => {
    const { track, grant } = rig(), showToast = vi.fn();
    global.MediaRecorder = class { constructor() { throw new Error('unsupported recorder'); } };
    const { media } = make({ showToast }); media.pressMic(); await grant();
    await vi.waitFor(() => expect(showToast).toHaveBeenCalledWith('voiceMsg.recordFailed', 'error'));
    expect(track.stop).toHaveBeenCalled();
  });

  it('a release before the mic opens still stops (and sends) once it does', async () => {
    const { track, grant } = rig();
    const now = vi.spyOn(Date, 'now');
    const { media, sent } = make();
    now.mockReturnValue(1000); media.pressMic();
    now.mockReturnValue(1600); media.releaseMic();      // held past the tap threshold
    now.mockReturnValue(1600); await grant();
    expect(media.state.mediaRecorder.state).toBe('inactive');
    expect(track.stop).toHaveBeenCalled();
    expect(sent).toHaveLength(0);                       // opened and stopped at once: too short
    now.mockRestore();
  });

  it('a tap keeps recording; a second tap sends with the real duration', async () => {
    const { track, grant } = rig();
    const now = vi.spyOn(Date, 'now');
    const { media, sent } = make();
    now.mockReturnValue(1000); media.pressMic();
    now.mockReturnValue(1100); media.releaseMic();      // a tap
    await grant();
    expect(media.state.mediaRecorder.state).toBe('recording');
    now.mockReturnValue(3100); media.pressMic();        // second tap
    media.releaseMic();
    expect(track.stop).toHaveBeenCalled();
    expect(sent).toHaveLength(1);
    expect(sent[0].duration_ms).toBe(2000);
    now.mockRestore();
  });

  it('a recording under half a second is discarded with a message', async () => {
    const { grant } = rig();
    const toasts = [];
    const now = vi.spyOn(Date, 'now');
    const { media, sent } = make({ showToast: (m) => toasts.push(m) });
    now.mockReturnValue(1000); media.pressMic(); media.releaseMic();
    await grant();
    now.mockReturnValue(1200); media.pressMic();
    expect(sent).toHaveLength(0);
    expect(toasts).toEqual(['voiceMsg.tooShort']);
    now.mockRestore();
  });
});

describe('startScreenShare', () => {
  it('is a no-op when there is no pre-negotiated video sender', async () => {
    const { media, sent } = make({ voiceState: { pc: null, currentCall: null } });
    await media.startScreenShare();
    expect(media.state.isSharing).toBe(false);
    expect(sent).toHaveLength(0);
  });
  it('replaceTracks the screen into the video sender and signals screenshare_started', async () => {
    const track = { kind: 'video', onended: null };
    const stream = { getVideoTracks: () => [track], getTracks: () => [track] };
    global.navigator = { mediaDevices: { getDisplayMedia: () => Promise.resolve(stream) } };
    // R80: screen share fans out via the pre-negotiated video sender (no addTrack).
    const sender = { replaceTrack: vi.fn() };
    const { media, sent } = make({ voiceState: { pc: null, _videoSender: sender, _peerVideoSenders: {}, currentCall: { session_id: 's1' } } });
    await media.startScreenShare();
    expect(media.state.isSharing).toBe(true);
    expect(sender.replaceTrack).toHaveBeenCalledWith(track);
    expect(sent).toContainEqual({ cmd: 'screenshare_started', session_id: 's1' });
  });
  it('fans the screen out to every group peer sender', async () => {
    const track = { kind: 'video', onended: null };
    const stream = { getVideoTracks: () => [track], getTracks: () => [track] };
    global.navigator = { mediaDevices: { getDisplayMedia: () => Promise.resolve(stream) } };
    const s1 = { replaceTrack: vi.fn() }, s2 = { replaceTrack: vi.fn() };
    const { media } = make({ voiceState: { pc: null, _videoSender: null, _peerVideoSenders: { a: s1, b: s2 }, currentCall: null } });
    await media.startScreenShare();
    expect(s1.replaceTrack).toHaveBeenCalledWith(track);
    expect(s2.replaceTrack).toHaveBeenCalledWith(track);
  });
});

describe('stopScreenShare', () => {
  it('stops tracks, restores the sender, clears state, and signals screenshare_stopped', () => {
    const stop = vi.fn();
    const sender = { replaceTrack: vi.fn() };
    const { media, sent } = make({ voiceState: { pc: null, _videoSender: sender, _peerVideoSenders: {}, videoEnabled: false, _cameraTrack: null, currentCall: { session_id: 's1' } } });
    media.state.isSharing = true;
    media.state.screenStream = { getTracks: () => [{ stop }] };
    media.stopScreenShare();
    expect(media.state.isSharing).toBe(false);
    expect(stop).toHaveBeenCalled();
    expect(sender.replaceTrack).toHaveBeenCalledWith(null);   // camera off -> clear
    expect(media.state.screenStream).toBe(null);
    expect(sent).toContainEqual({ cmd: 'screenshare_stopped', session_id: 's1' });
  });
});
