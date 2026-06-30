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
    classList: { toggle() {}, add() {}, remove() {} },
    querySelectorAll: () => [], appendChild() {}, remove() {},
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
    showToast: () => {}, renderMessage: () => {}, showOsNotification: () => {},
    sendCmd: () => {}, playNotificationSound: () => {}, normalizeRelayThreadId: (e) => e,
    stopScreenShare: () => {},
    getSocket: () => socket, getActiveView: () => over.activeView ?? null,
    getSelfWebId: () => 'did:key:zSelf', getTurnUrl: () => null, getTurnSecret: () => null,
    getLocalDmPeers: () => over.localDmPeers ?? {}, getCurrentRoomMembers: () => over.members ?? [],
    getIsSharing: () => false,
  });
  return { voice, sent };
}

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
