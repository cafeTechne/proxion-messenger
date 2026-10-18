import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest';
import { webcrypto } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createVoice, CallState, audioLevel } from './voice.js';
import { extractFingerprint, verifyFingerprint, ed25519PubToDid } from './callsec.js';

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
    getContactName: (did) => (over.contacts || {})[did] || '',
    getIsSharing: () => false,
    getExpectedPeerDid: () => over.expectedPeerDid ?? '',
    getDeviceCert: () => over.deviceCert ?? null,
    getPeerBindsCalls: () => over.peerBindsCalls ?? false,
    onPeerVerified: over.onPeerVerified ?? (() => {}),
    getUserRelay: () => over.userRelay ?? null,
    // Default relay is on unless a test opts out (to exercise the STUN-only path).
    getUseDefaultRelay: () => over.useDefaultRelay ?? true,
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
  it('joinVoice sends the join command and records the channel', async () => {
    const { voice, sent } = makeVoice();
    voice.state.localStream = { getTracks: () => [{ stop() {} }] };  // skip getUserMedia
    await voice.joinVoice('room-1');
    expect(sent).toContainEqual({ cmd: 'join_voice_channel', room_id: 'room-1' });
    expect(voice.state._inVoiceChannel).toBe('room-1');
  });
  it('joinVoice does not join without a microphone, and says why', async () => {
    const toasts = [];
    const { voice, sent } = makeVoice({ showToast: (m) => toasts.push(m) });
    const md = global.navigator?.mediaDevices;
    Object.defineProperty(global, 'navigator', { configurable: true, value: {
      ...(global.navigator || {}), mediaDevices: { getUserMedia: () => Promise.reject(new Error('denied')) } } });
    try {
      await voice.joinVoice('room-1');
    } finally {
      Object.defineProperty(global, 'navigator', { configurable: true, value: { ...(global.navigator || {}), mediaDevices: md } });
    }
    expect(sent).toHaveLength(0);
    expect(voice.state._inVoiceChannel).toBeFalsy();
    expect(toasts).toContain('voice.micRequired');
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

  it('the caller names the session it invites with', async () => {
    installRTC();
    const { voice, sent } = makeVoice();
    voice.state.localStream = { getTracks: () => [{ stop() {} }] };
    await voice.initWebRTC('cert1', null, true);
    const invite = sent.find((m) => m.cmd === 'voice_invite');
    expect(invite.session_id).toMatch(/^[0-9a-f]{32}$/);
    expect(voice.state.currentCallSessionId).toBe(invite.session_id);
  });

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
    // Disable the default public relay to exercise the genuinely-no-relay path.
    const { voice } = makeVoice({ showToast: (m) => toasts.push(m), useDefaultRelay: false });
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

  it('1:1 call: a call-CAPABLE peer whose answer has no binding proof is refused (downgrade)', async () => {
    installRTC();
    const toasts = [];
    // We know this contact binds calls (a pin or cert marker), so an answer stripped of
    // its binding proof is a downgrade, not a legacy client: refuse it.
    const { voice, sent } = makeVoice({
      showToast: (m) => toasts.push(m), expectedPeerDid: 'did:key:zPeer', peerBindsCalls: true,
    });
    voice.state.localStream = { getTracks: () => [{ stop() {} }] };
    await voice.initWebRTC('cert1', 'sess1', true);

    const answerSdp = 'v=0\r\na=fingerprint:sha-256 AA:BB:CC:DD\r\n';
    await voice.handleVoiceAnswer({ sdp_answer: answerSdp, fp_sig: '', fp_signer: '' });

    expect(toasts).toContain('voice.identityUnverified');
    expect(sent.some((m) => m.cmd === 'voice_hangup')).toBe(true);
    expect(voice.state._callState).not.toBe(CallState.CONNECTED);
  });

  it('1:1 call: the SAME stripped answer connects (Unverified) for a non-capable peer', async () => {
    installRTC();
    const { voice } = makeVoice({ expectedPeerDid: 'did:key:zPeer', peerBindsCalls: false });
    voice.state.localStream = { getTracks: () => [{ stop() {} }] };
    await voice.initWebRTC('cert1', 'sess1', true);

    const answerSdp = 'v=0\r\na=fingerprint:sha-256 AA:BB:CC:DD\r\n';
    await voice.handleVoiceAnswer({ sdp_answer: answerSdp, fp_sig: '', fp_signer: '' });

    expect(voice.state._verifyState).toBe('unverified');
    expect(voice.state._callState).toBe(CallState.CONNECTED);   // allowed, not refused
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

describe('inbound signal binding (pod call-inbox is untrusted)', () => {
  function inActiveCall(voice, { session = 'sess1', peer = 'did:key:zPeer' } = {}) {
    voice.state.currentCallSessionId = session;
    voice.state._callPeerWebid = peer;
    voice.setCallState(CallState.CALLING);      // IDLE → CALLING → CONNECTED (valid path)
    voice.setCallState(CallState.CONNECTED);
  }
  const stillUp = (voice, session = 'sess1') =>
    voice.state.currentCallSessionId === session && voice.state._callState === CallState.CONNECTED;

  it('hangup: a matching session_id + peer tears the call down', () => {
    const { voice } = makeVoice();
    inActiveCall(voice);
    voice.handleVoiceHangup({ session_id: 'sess1', from_webid: 'did:key:zPeer' });
    expect(voice.state.currentCallSessionId).toBe(null);
    expect(voice.state._callState).toBe(CallState.IDLE);
  });

  it('hangup: a stale/other session_id does NOT tear down the active call', () => {
    const { voice } = makeVoice();
    inActiveCall(voice);
    voice.handleVoiceHangup({ session_id: 'old-session', from_webid: 'did:key:zPeer' });
    expect(stillUp(voice)).toBe(true);
  });

  it('hangup: a pod signal from the WRONG WebID does NOT tear down the call', () => {
    const { voice } = makeVoice();
    inActiveCall(voice);
    voice.handleVoiceHangup({ session_id: 'sess1', from_webid: 'did:key:zAttacker' });
    expect(stillUp(voice)).toBe(true);
  });

  it('hangup: an authenticated gateway relay (gateway did, _fromGateway) still tears down', () => {
    const { voice } = makeVoice();
    inActiveCall(voice);
    // Cross-gateway relay stamps the relaying gateway did, not the peer account webid.
    voice.handleVoiceHangup({ session_id: 'sess1', from_webid: 'did:key:zGateway', _fromGateway: true });
    expect(voice.state.currentCallSessionId).toBe(null);
    expect(voice.state._callState).toBe(CallState.IDLE);
  });

  it('hangup: a same-gateway hangup (no from_webid) still tears down', () => {
    const { voice } = makeVoice();
    inActiveCall(voice);
    voice.handleVoiceHangup({ session_id: 'sess1' });
    expect(voice.state.currentCallSessionId).toBe(null);
    expect(voice.state._callState).toBe(CallState.IDLE);
  });

  it('hangup: NEITHER session_id NOR from_webid is REJECTED (the drop-box exploit)', () => {
    const { voice } = makeVoice();
    inActiveCall(voice);
    // A bare {"type":"voice_hangup"} POSTed to the public-Append call-inbox proves
    // nothing about the call and must not tear it down (fail closed).
    voice.handleVoiceHangup({});
    expect(stillUp(voice)).toBe(true);
  });

  it('hangup: naming the peer but WITHOUT the session_id secret is REJECTED', () => {
    const { voice } = makeVoice();
    inActiveCall(voice);
    // The peer WebID is public and self-attested over the pod; the session_id is the
    // secret gate, so a hangup that omits it is rejected even if it names the peer.
    voice.handleVoiceHangup({ from_webid: 'did:key:zPeer' });
    expect(stillUp(voice)).toBe(true);
  });

  it('ice: a candidate from a non-peer WebID is ignored', async () => {
    const { voice } = makeVoice();
    const added = [];
    voice.state.pc = { addIceCandidate: (c) => { added.push(c); return Promise.resolve(); } };
    voice.state._remoteDescSet = true;
    voice.state._callPeerWebid = 'did:key:zPeer';

    await voice.handleIceCandidate({ from_webid: 'did:key:zAttacker', candidate: 'evil' });
    expect(added).toHaveLength(0);

    await voice.handleIceCandidate({ from_webid: 'did:key:zPeer', candidate: 'legit' });
    expect(added).toHaveLength(1);
  });

  it('ice: an omitted from_webid on the pod path is REJECTED (binding not bypassed)', async () => {
    const { voice } = makeVoice();
    const added = [];
    voice.state.pc = { addIceCandidate: (c) => { added.push(c); return Promise.resolve(); } };
    voice.state._remoteDescSet = true;
    voice.state._callPeerWebid = 'did:key:zPeer';
    voice.state.currentCallSessionId = 'sess1';

    // No from_webid and no matching session_id: an injected pod candidate. Rejected.
    await voice.handleIceCandidate({ candidate: 'evil' });
    expect(added).toHaveLength(0);

    // A gateway-relayed candidate (gateway did, _fromGateway) still applies.
    await voice.handleIceCandidate({ _fromGateway: true, from_webid: 'did:key:zGateway', candidate: 'relayed' });
    expect(added).toHaveLength(1);

    // A same-gateway candidate (no from_webid, matching session_id) still applies.
    await voice.handleIceCandidate({ session_id: 'sess1', candidate: 'samegw' });
    expect(added).toHaveLength(2);
  });

  it('answer: an omitted from_webid on the pod path is REJECTED; legit paths still apply', async () => {
    const { voice } = makeVoice();
    const setRemote = [];
    voice.state.pc = {
      setRemoteDescription: (d) => { setRemote.push(d); return Promise.resolve(); },
      getTransceivers: () => [],
    };
    voice.state._callPeerWebid = 'did:key:zPeer';
    voice.state.currentCallSessionId = 'sess1';
    const answerSdp = 'v=0\r\na=fingerprint:sha-256 AA:BB:CC:DD\r\n';

    // No from_webid and no matching session_id: an injected pod answer. Rejected before
    // setRemoteDescription.
    await voice.handleVoiceAnswer({ sdp_answer: answerSdp });
    expect(setRemote).toHaveLength(0);

    // A legit same-peer pod answer proceeds.
    await voice.handleVoiceAnswer({ from_webid: 'did:key:zPeer', sdp_answer: answerSdp });
    expect(setRemote).toHaveLength(1);

    // A gateway-relayed answer (gateway did, _fromGateway) proceeds too.
    await voice.handleVoiceAnswer({ _fromGateway: true, from_webid: 'did:key:zGateway', sdp_answer: answerSdp });
    expect(setRemote).toHaveLength(2);
  });
});

describe('_getIceServers records whether a relay is available', () => {
  it('STUN-only (no /turn-credentials, no client TURN) → _turnConfigured false', async () => {
    const savedFetch = global.fetch;
    global.fetch = undefined;                       // no relay endpoint reachable
    const { voice } = makeVoice({ useDefaultRelay: false });   // and no default public relay
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

  it('the free default relay alone makes _turnConfigured true (R91)', async () => {
    const savedFetch = global.fetch;
    global.fetch = undefined;                        // no gateway/client TURN
    const { voice } = makeVoice();                   // default relay ON
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

// CF1: the DTLS-fingerprint signature binds to the peer's ACCOUNT identity, not the
// routable _callPeerWebid. Cross-gateway the latter is the caller's GATEWAY did (which
// the far side never verifies against), so the recipient binding must use the account
// WebID the far side knows itself by, or it would fall back to the unbound legacy form.
describe('cross-gateway fingerprint binding uses the account identity (CF1)', () => {
  it('callee binds to the caller ACCOUNT WebID while routing uses the gateway did', async () => {
    const { voice } = makeVoice({ activeView: { peerWebid: 'did:key:zAlice' } });
    voice.state.currentCall = { caller_gateway: 'did:key:zGATEWAY', caller_webid: 'did:key:zAlice' };
    voice.state.localStream = { getTracks: () => [{ stop() {} }] };   // skip getUserMedia
    try { await voice.initWebRTC('cert1', 'sess1', false); } catch { /* later RTC steps irrelevant */ }
    expect(voice.state._callPeerWebid).toBe('did:key:zGATEWAY');   // routing
    expect(voice.state._callPeerIdentity).toBe('did:key:zAlice');  // identity the fp binds to
  });

  it('same-gateway callee collapses routing and identity to the account did', async () => {
    const { voice } = makeVoice({ activeView: { peerWebid: 'did:key:zAlice' } });
    voice.state.currentCall = { caller_webid: 'did:key:zAlice' };   // no separate gateway hop
    voice.state.localStream = { getTracks: () => [{ stop() {} }] };
    try { await voice.initWebRTC('cert1', 'sess1', false); } catch { /* ignore */ }
    expect(voice.state._callPeerWebid).toBe('did:key:zAlice');
    expect(voice.state._callPeerIdentity).toBe('did:key:zAlice');
  });
});

// Ringing / decline / busy / glare: every way a call ends reaches the other side,
// and nothing leaves a stuck "Calling..." or a banner that rings on.
describe('call lifecycle signaling', () => {
  beforeEach(() => { global.WebSocket = { OPEN: 1 }; });
  const banner = () => document.getElementById('voice-banner');
  const invite = (over = {}) => ({ session_id: 'inv1', caller_webid: 'did:key:zAlice', ...over });

  it('caller cancelling while it rings stops the callee banner and notes a missed call', () => {
    const toasts = [];
    const { voice } = makeVoice({ showToast: (m) => toasts.push(m) });
    voice.showVoiceBanner(invite({ caller_gateway: 'did:key:zGW' }));
    expect(banner().style.display).toBe('flex');
    voice.handleVoiceHangup({ session_id: 'inv1', from_webid: 'did:key:zGW', _fromGateway: true });
    expect(voice.state._callState).toBe(CallState.IDLE);
    expect(voice.state.currentCall).toBe(null);
    expect(banner().style.display).toBe('none');
    expect(toasts.some((m) => /missed/i.test(m))).toBe(true);
  });

  it('a hangup for some other session does not stop the ring', () => {
    const { voice } = makeVoice();
    voice.showVoiceBanner(invite());
    voice.handleVoiceHangup({ session_id: 'other', _fromGateway: true });
    expect(voice.state._callState).toBe(CallState.RINGING);
  });

  it('declining tells the caller (routed to the caller gateway) and clears the banner', () => {
    const { voice, sent } = makeVoice();
    voice.showVoiceBanner(invite({ caller_gateway: 'did:key:zGW' }));
    voice.declineCall();
    expect(sent).toContainEqual(expect.objectContaining({
      cmd: 'voice_hangup', session_id: 'inv1', target_webid: 'did:key:zGW', reason: 'declined' }));
    expect(voice.state._callState).toBe(CallState.IDLE);
    expect(banner().style.display).toBe('none');
  });

  it('end-call while ringing is a decline too', () => {
    const { voice, sent } = makeVoice();
    voice.showVoiceBanner(invite());
    voice._doHangup();
    expect(sent).toContainEqual(expect.objectContaining({ cmd: 'voice_hangup', session_id: 'inv1', reason: 'declined' }));
  });

  it('the caller is told why: declined, busy, or ended', () => {
    for (const [reason, re] of [['declined', /declined/i], ['busy', /busy|another call/i], [undefined, /declined/i]]) {
      const toasts = [];
      const { voice } = makeVoice({ showToast: (m) => toasts.push(m) });
      voice.state.currentCallSessionId = 's1';
      voice.setCallState(CallState.CALLING);
      voice.handleVoiceHangup({ session_id: 's1', reason });
      expect(voice.state._callState).toBe(CallState.IDLE);
      expect(toasts.some((m) => re.test(m))).toBe(true);
    }
    const toasts = [];
    const { voice } = makeVoice({ showToast: (m) => toasts.push(m) });
    voice.state.currentCallSessionId = 's1';
    voice.setCallState(CallState.CALLING);
    voice.setCallState(CallState.CONNECTED);
    voice.handleVoiceHangup({ session_id: 's1' });
    expect(toasts.some((m) => /ended/i.test(m))).toBe(true);
  });

  it('a second invite during a live call is turned away as busy, the call untouched', () => {
    const { voice, sent } = makeVoice();
    voice.state.currentCallSessionId = 'live';
    voice.state._callPeerWebid = 'did:key:zBob';
    voice.setCallState(CallState.CALLING);
    voice.setCallState(CallState.CONNECTED);
    voice.showVoiceBanner(invite({ session_id: 'inv2', caller_webid: 'did:key:zCarol' }));
    expect(sent).toContainEqual(expect.objectContaining({ cmd: 'voice_hangup', session_id: 'inv2', reason: 'busy' }));
    expect(voice.state._callState).toBe(CallState.CONNECTED);
    expect(voice.state.currentCallSessionId).toBe('live');
    expect(voice.state._callPeerWebid).toBe('did:key:zBob');
    expect(voice.state.currentCall).toBe(null);
  });

  it('glare: both call each other, the lower session id survives on both sides', () => {
    // This side's call has the LOWER id: keep calling, ignore the crossing invite.
    const a = makeVoice();
    a.voice.state.currentCallSessionId = 'aaa';
    a.voice.state._callPeerWebid = 'did:key:zBob';
    a.voice.setCallState(CallState.CALLING);
    a.voice.showVoiceBanner(invite({ session_id: 'bbb', caller_webid: 'did:key:zBob' }));
    expect(a.voice.state._callState).toBe(CallState.CALLING);
    expect(a.voice.state.currentCallSessionId).toBe('aaa');
    expect(a.sent.some((m) => m.cmd === 'voice_hangup')).toBe(false);
    // This side's call has the HIGHER id: drop it and ring for the other one.
    const b = makeVoice();
    b.voice.state.currentCallSessionId = 'bbb';
    b.voice.state._callPeerWebid = 'did:key:zAlice';
    b.voice.setCallState(CallState.CALLING);
    b.voice.showVoiceBanner(invite({ session_id: 'aaa', caller_webid: 'did:key:zAlice' }));
    expect(b.sent).toContainEqual(expect.objectContaining({ cmd: 'voice_hangup', session_id: 'bbb' }));
    expect(b.voice.state._callState).toBe(CallState.RINGING);
    expect(b.voice.state.currentCall.session_id).toBe('aaa');
  });

  it('an old ring timeout does not cut off a newer call', () => {
    vi.useFakeTimers();
    try {
      const { voice } = makeVoice();
      voice.showVoiceBanner(invite({ session_id: 'one' }));
      vi.advanceTimersByTime(20000);
      voice.declineCall();
      voice.showVoiceBanner(invite({ session_id: 'two' }));
      vi.advanceTimersByTime(15000);   // past the first ring's 30s mark
      expect(voice.state._callState).toBe(CallState.RINGING);
      expect(voice.state.currentCall.session_id).toBe('two');
      vi.advanceTimersByTime(16000);   // the second ring's own timeout
      expect(voice.state._callState).toBe(CallState.IDLE);
    } finally { vi.useRealTimers(); }
  });

  it('a refused invite (throttle, not a contact) ends "Calling..." at once', () => {
    const { voice } = makeVoice();
    voice.state.currentCallSessionId = 's1';
    voice.setCallState(CallState.CALLING);
    expect(voice.handleCallError('room_full')).toBe(false);
    expect(voice.state._callState).toBe(CallState.CALLING);
    expect(voice.handleCallError('call_too_frequent')).toBe(true);
    expect(voice.state._callState).toBe(CallState.IDLE);
    expect(voice.handleCallError('call_too_frequent')).toBe(false);   // idle: nothing to end
  });
});

// F4: the incoming-call banner named the caller by a raw did:key prefix even
// when the caller was a contact: across gateways the invite's caller_webid is
// the caller's browser key, while the contact is known by the (authenticated)
// relaying gateway did. Both are looked up, contacts included, and the label
// carries the same id tag the contact row shows.
describe('caller naming (F4)', () => {
  const GW = 'did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK';
  const BROWSER = 'did:key:z6MkmdPx39wGDaTCfUunQwErTyUiOpAsDfGhJkLzXcVbNm';
  it('rings a cross-gateway contact as known, labelled by the contact name and tag', () => {
    const { voice } = makeVoice({ contacts: { [GW]: 'Alice' } });
    voice.handleVoiceSignalRelay({ signal_type: 'offer', session_id: 's1', from_webid: GW,
      signal_data: { caller_webid: BROWSER, sdp_offer: 'v=0' } });
    expect(document.getElementById('voice-msg').innerText).toBe('voice.incomingFrom');
    expect(voice._callerLabel(BROWSER, GW)).toBe('Alice ·nnEGta2doK');
  });
  it('never shows a raw did:key prefix for an unknown caller', () => {
    const { voice } = makeVoice();
    expect(voice._callerLabel(BROWSER)).toBe('did:key:…' + BROWSER.slice(-10));
    expect(voice._callerDisplayName(BROWSER)).not.toMatch(/^did:key:z6Mk/);
  });
  it('the call widget shows the contact name, not the key', () => {
    const { voice } = makeVoice({ contacts: { [GW]: 'Alice' } });
    expect(voice._callerDisplayName(GW)).toBe('Alice');
  });
});

// F3: the voice channel panel lists everyone as a tile, yourself included, with
// your mute state, so a lone participant still sees who is in the channel.
describe('voice channel tiles (F3)', () => {
  it('renders a self tile with the muted state, and one tile per remote peer', async () => {
    const { voice } = makeVoice({ members: [{ webid: 'did:key:z6MkBobBobBobBobBob', display_name: 'Bob' }] });
    voice.state.localStream = { getTracks: () => [{ stop() {} }], getAudioTracks: () => [] };
    await voice.joinVoice('room-1');
    voice.state.isMuted = true;
    voice._addChannelParticipant('did:key:z6MkBobBobBobBobBob');
    const html = document.getElementById('voice-channel-participants').innerHTML;
    expect(html).toContain('data-vc-self');
    expect(html).toContain('ui.muted');
    expect(html).toMatch(/class="vc-tile[^"]*"[^>]*data-vc-webid="did:key:z6MkBobBobBobBobBob"/);
    expect(html).toContain('Bob');
    // The self tile carries no data-vc-webid, so remote-peer counts stay exact.
    expect((html.match(/data-vc-webid=/g) || []).length).toBe(1);
  });
});

// F3 layout: the voice channel panel used to be position:fixed across the
// bottom of the window, over the composer; the 1:1 call widget's controls row
// did not wrap, so in the 260px sidebar the hang-up button was clipped.
describe('call layout (F3)', () => {
  const css = readFileSync(new URL('./style.css', import.meta.url), 'utf8');
  const html = readFileSync(new URL('./index.html', import.meta.url), 'utf8');
  it('the voice channel panel sits in the chat column, not fixed over the composer', () => {
    const tag = html.match(/<div id="voice-channel-panel"[^>]*>/)?.[0] || '';
    expect(tag).not.toMatch(/position\s*:\s*fixed/);
    const rule = css.match(/#voice-channel-panel\s*\{[^}]*\}/)?.[0] || '';
    expect(rule).not.toMatch(/position\s*:\s*fixed/);
    // In the DOM it follows the chat header and precedes the feed.
    expect(html.indexOf('id="voice-channel-panel"')).toBeLessThan(html.indexOf('id="message-feed"'));
  });
  it('the call widget controls wrap so the hang-up button stays in view', () => {
    const rule = css.match(/\.vw-controls\s*\{[^}]*\}/)?.[0] || '';
    expect(rule).toMatch(/flex-wrap\s*:\s*wrap/);
    expect(css).toMatch(/\.vw-controls\s*>\s*#vw-quality\s*\{[^}]*flex:\s*1 0 100%/);
  });
});
