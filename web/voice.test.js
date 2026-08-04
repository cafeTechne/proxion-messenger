import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createVoice, CallState, audioLevel } from './voice.js';

describe('audioLevel (speaking detection)', () => {
  it('is ~0 for silence (all samples at the 128 midpoint)', () => {
    expect(audioLevel(new Uint8Array(256).fill(128))).toBeCloseTo(0, 5);
  });
  it('rises with amplitude and exceeds the speaking threshold for loud audio', () => {
    const quiet = new Uint8Array(256).map((_, i) => 128 + (i % 2 ? 4 : -4));   // tiny wobble
    const loud  = new Uint8Array(256).map((_, i) => (i % 2 ? 255 : 0));         // full-scale
    expect(audioLevel(loud)).toBeGreaterThan(audioLevel(quiet));
    expect(audioLevel(loud)).toBeGreaterThan(0.045);   // over the detector threshold
    expect(audioLevel(quiet)).toBeLessThan(0.045);
  });
  it('handles empty/missing input', () => {
    expect(audioLevel(new Uint8Array(0))).toBe(0);
    expect(audioLevel(null)).toBe(0);
  });
});

// DOM stub: getElementById returns a fake element so DOM-touching helpers
// (channel panel, leave button) don't throw in the node test env.
beforeEach(() => {
  const els = {};
  const mkEl = () => ({ id: '', style: {}, textContent: '', innerHTML: '',
    dataset: {}, srcObject: null, muted: false,
    classList: { toggle() {}, add() {}, remove() {} },
    querySelectorAll: () => [], appendChild() {}, remove() {},
    setAttribute() {}, removeAttribute() {}, getAttribute() { return null; },
    play() { return Promise.resolve(); },
    setProperty() {}, addEventListener() {} });
  global.document = {
    getElementById: (id) => (els[id] ||= mkEl()),
    createElement: () => mkEl(),
    body: { appendChild() {}, prepend() {} },
  };
});

function makeVoice(over = {}) {
  const sent = [];
  const socket = { send: (s) => sent.push(JSON.parse(s)), readyState: 1 };
  const voice = createVoice({
    showToast: over.showToast ?? (() => {}), renderMessage: () => {}, showOsNotification: () => {},
    sendCmd: () => {}, playNotificationSound: () => {}, normalizeRelayThreadId: (e) => e,
    stopScreenShare: () => {},
    getSocket: () => socket, getActiveView: () => over.activeView ?? null,
    getSelfWebId: () => 'did:key:zSelf', getTurnUrl: () => null, getTurnSecret: () => null,
    getLocalDmPeers: () => over.localDmPeers ?? {}, getCurrentRoomMembers: () => over.members ?? [],
    getIsSharing: () => false,
    getExpectedPeerDid: () => over.expectedPeerDid ?? '',
    getDeviceCert: () => over.deviceCert ?? null,
  });
  return { voice, sent };
}

describe('video quality (R81 P2)', () => {
  function mockSender() {
    return {
      _p: { encodings: [{}] },
      replaceTrack: () => Promise.resolve(),
      getParameters() { return this._p; },
      setParameters(p) { this._p = p; return Promise.resolve(); },
    };
  }

  it('setQualityProfile persists and re-caps live senders', async () => {
    const store = {};
    global.localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); } };
    const { voice } = makeVoice();
    const s = mockSender();
    voice.state._videoSender = s;              // a live 1:1 sender
    voice.setQualityProfile('saver');
    await new Promise((r) => setTimeout(r, 0));
    expect(store['proxion_call_quality']).toBe('saver');
    // 1:1 saver ceiling is 300 kbps.
    expect(s._p.encodings[0].maxBitrate).toBe(300000);
    voice.setQualityProfile('high');
    await new Promise((r) => setTimeout(r, 0));
    expect(s._p.encodings[0].maxBitrate).toBe(2500000);
  });
});

describe('CallState enum', () => {
  it('is a frozen set of states', () => {
    expect(CallState.IDLE).toBe('idle');
    expect(Object.isFrozen(CallState)).toBe(true);
  });
});

describe('joinVoice / leaveVoiceChannel', () => {
  it('joinVoice sends the join command and records the channel', () => {
    const { voice, sent } = makeVoice();
    voice.joinVoice('room-1');
    expect(sent).toContainEqual({ cmd: 'join_voice_channel', room_id: 'room-1' });
    expect(voice.state._inVoiceChannel).toBe('room-1');
  });
  it('leaveVoiceChannel sends leave, clears channel, closes peers', () => {
    const { voice, sent } = makeVoice();
    voice.state._inVoiceChannel = 'room-1';
    const closed = vi.fn();
    voice.state.peerConnections['did:key:zBob'] = { close: closed };
    voice.leaveVoiceChannel();
    expect(sent).toContainEqual({ cmd: 'leave_voice_channel', room_id: 'room-1' });
    expect(voice.state._inVoiceChannel).toBe(null);
    expect(closed).toHaveBeenCalled();
    expect(Object.keys(voice.state.peerConnections)).toHaveLength(0);
  });
  it('leaveVoiceChannel is a no-op when not in a channel', () => {
    const { voice, sent } = makeVoice();
    voice.leaveVoiceChannel();
    expect(sent).toHaveLength(0);
  });
});

describe('channel participant tracking', () => {
  it('adds and removes participants in state', () => {
    const { voice } = makeVoice();
    voice._addChannelParticipant('did:key:zBob');
    expect(voice.state._channelParticipants['did:key:zBob']).toBeTruthy();
    voice._removeChannelParticipant('did:key:zBob');
    expect(voice.state._channelParticipants['did:key:zBob']).toBeUndefined();
  });

  it('updates a participant connection-state (regression: param no longer shadows cluster state)', () => {
    const { voice } = makeVoice();
    voice._addChannelParticipant('did:key:zBob');
    voice._updateChannelParticipantUI('did:key:zBob', 'failed');
    expect(voice.state._channelParticipants['did:key:zBob'].state).toBe('failed');
    voice._updateChannelParticipantUI('did:key:zBob', 'connected');
    expect(voice.state._channelParticipants['did:key:zBob'].state).toBe('connected');
  });
});

describe('handleVoicePeerLeft cleanup', () => {
  it('closes and forgets the departed peer', () => {
    const { voice } = makeVoice();
    const closed = vi.fn();
    voice.state.peerConnections['did:key:zBob'] = { close: closed };
    voice.state.peerAudioElements['did:key:zBob'] = { srcObject: {} };
    voice._addChannelParticipant('did:key:zBob');
    voice.handleVoicePeerLeft({ peer_webid: 'did:key:zBob' });
    expect(closed).toHaveBeenCalled();
    expect(voice.state.peerConnections['did:key:zBob']).toBeUndefined();
    expect(voice.state._channelParticipants['did:key:zBob']).toBeUndefined();
  });
});

describe('ICE-failure handling (silent-drop regressions)', () => {
  const flush = () => new Promise((r) => setTimeout(r, 0));

  function installRTC() {
    class FakePC {
      constructor() { this.iceConnectionState = 'new'; }
      addTrack() {}
      addTransceiver() { return { sender: { replaceTrack() { return Promise.resolve(); } } }; }
      getSenders() { return []; }
      async createOffer() { return { type: 'offer', sdp: 'o' }; }
      async createAnswer() { return { type: 'answer', sdp: 'a' }; }
      async setLocalDescription() {}
      async setRemoteDescription() {}
      restartIce() {}
      close() {}
    }
    global.RTCPeerConnection = FakePC;
    global.WebSocket = { OPEN: 1 };
  }

  it('1:1 call: ICE "failed" surfaces a toast and hangs up (was fully silent)', async () => {
    installRTC();
    const toasts = [];
    const { voice, sent } = makeVoice({ showToast: (m) => toasts.push(m) });
    voice.state.localStream = { getTracks: () => [{ stop() {} }] };  // skip getUserMedia

    await voice.initWebRTC('cert1', 'sess1', true);
    expect(sent.some((m) => m.cmd === 'voice_invite')).toBe(true);

    const pc = voice.state.pc;
    pc.iceConnectionState = 'failed';
    pc.oniceconnectionstatechange();

    expect(toasts.length).toBeGreaterThan(0);
    expect(sent.some((m) => m.cmd === 'voice_hangup')).toBe(true);
  });

  it('1:1 call: with no relay, the failure toast points at the real fix', async () => {
    installRTC();
    const toasts = [];
    const { voice } = makeVoice({ showToast: (m) => toasts.push(m) });
    voice.state.localStream = { getTracks: () => [{ stop() {} }] };

    await voice.initWebRTC('cert1', 'sess1', true);   // no fetch, no client TURN → STUN only
    expect(voice.state._turnConfigured).toBe(false);

    const pc = voice.state.pc;
    pc.iceConnectionState = 'failed';
    pc.oniceconnectionstatechange();

    // The no-relay variant (key returned verbatim since no locale is loaded in tests).
    expect(toasts).toContain('voice.noRelay');
    expect(toasts).not.toContain('voice.connectionLost');
  });

  it('1:1 call: REFUSES a tampered media channel (bound signer, bad fingerprint sig)', async () => {
    installRTC();
    const toasts = [];
    // The contact IS the signer (signerDid === the identity we expect), but the
    // fingerprint signature does not check out: a relay swapped the DTLS fingerprint.
    // That is the one case we refuse.
    const peer = 'did:key:zPeer';
    const { voice, sent } = makeVoice({ showToast: (m) => toasts.push(m), expectedPeerDid: peer });
    voice.state.localStream = { getTracks: () => [{ stop() {} }] };
    await voice.initWebRTC('cert1', 'sess1', true);

    const answerSdp = 'v=0\r\na=fingerprint:sha-256 AA:BB:CC:DD\r\n';
    await voice.handleVoiceAnswer({ sdp_answer: answerSdp, fp_sig: 'not-a-valid-signature', fp_signer: peer });

    // The call is refused: the user is told, and the session is torn down (cleanup
    // resets _verifyState, so the observable signals are the toast and the hangup).
    expect(toasts).toContain('voice.identityUnverified');
    expect(sent.some((m) => m.cmd === 'voice_hangup')).toBe(true);
    expect(voice.state._callState).not.toBe(CallState.CONNECTED);
  });

  it('1:1 call: an UNKNOWN peer (no expected identity) still connects, marked unverified', async () => {
    installRTC();
    const { voice } = makeVoice({ expectedPeerDid: '' });   // we do not know this peer
    voice.state.localStream = { getTracks: () => [{ stop() {} }] };
    await voice.initWebRTC('cert1', 'sess1', true);

    const answerSdp = 'v=0\r\na=fingerprint:sha-256 AA:BB:CC:DD\r\n';
    await voice.handleVoiceAnswer({ sdp_answer: answerSdp, fp_sig: '', fp_signer: '' });

    expect(voice.state._verifyState).toBe('unverified');   // allowed, not refused
    expect(voice.state._callState).toBe(CallState.CONNECTED);
  });

  it('group call: the CALLER side rebuilds the peer on ICE "failed" (was a no-op restartIce)', async () => {
    installRTC();
    const { voice, sent } = makeVoice();
    voice.state._inVoiceChannel = 'room1';
    voice.state.localStream = { getTracks: () => [{ stop() {} }] };

    await voice.initWebRTCForPeer('did:key:zBob', 'sess1', /* isCaller */ true);
    const inviteCount = () => sent.filter((m) => m.cmd === 'voice_invite').length;
    expect(inviteCount()).toBe(1);

    const pc = voice.state.peerConnections['did:key:zBob'];
    pc.iceConnectionState = 'failed';
    pc.oniceconnectionstatechange();
    await flush();

    expect(inviteCount()).toBe(2);                       // re-invited → real ICE restart
    expect(voice.state.peerConnections['did:key:zBob']._proxionReconnects).toBe(1);
  });

  it('group call: the CALLEE side does NOT re-invite on failure (avoids glare)', async () => {
    installRTC();
    const { voice, sent } = makeVoice();
    voice.state._inVoiceChannel = 'room1';
    voice.state.localStream = { getTracks: () => [{ stop() {} }] };

    await voice.initWebRTCForPeer('did:key:zBob', 'sess1', /* isCaller */ false, 'remote-offer');
    expect(sent.some((m) => m.cmd === 'voice_answer')).toBe(true);
    expect(sent.some((m) => m.cmd === 'voice_invite')).toBe(false);

    const pc = voice.state.peerConnections['did:key:zBob'];
    pc.iceConnectionState = 'failed';
    pc.oniceconnectionstatechange();
    await flush();

    expect(sent.some((m) => m.cmd === 'voice_invite')).toBe(false);   // still no invite
  });
});

describe('_getIceServers records whether a relay is available', () => {
  it('STUN-only (no /turn-credentials, no client TURN) → _turnConfigured false', async () => {
    const savedFetch = global.fetch;
    global.fetch = undefined;                       // no relay endpoint reachable
    const { voice } = makeVoice();                  // getTurnUrl/getTurnSecret return null
    const servers = await voice._getIceServers();
    expect(servers.some((s) => String(s.urls).startsWith('stun:'))).toBe(true);
    expect(voice.state._turnConfigured).toBe(false);
    global.fetch = savedFetch;
  });

  it('a turn: server from /turn-credentials → _turnConfigured true', async () => {
    const savedFetch = global.fetch;
    global.fetch = () => Promise.resolve({
      json: () => Promise.resolve({ urls: ['turn:relay.example:3478'], username: 'u', credential: 'c' }),
    });
    const { voice } = makeVoice();
    await voice._getIceServers();
    expect(voice.state._turnConfigured).toBe(true);
    global.fetch = savedFetch;
  });
});

describe('_callerDisplayName resolves via injected lookups', () => {
  it('prefers a known DM peer display name', () => {
    const { voice } = makeVoice({ localDmPeers: { t1: { peer_webid: 'did:key:zBob', display_name: 'Bob' } } });
    expect(voice._callerDisplayName('did:key:zBob')).toBe('Bob');
  });
  it('falls back to a room member name', () => {
    const { voice } = makeVoice({ members: [{ webid: 'did:key:zCarol', display_name: 'Carol' }] });
    expect(voice._callerDisplayName('did:key:zCarol')).toBe('Carol');
  });
});
