import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createRooms, setButtonBusy, clearButtonBusy, isButtonBusy } from './rooms.js';

let els;
function mkEl(over = {}) {
  return { style: {}, textContent: '', value: '', ...over };
}
beforeEach(() => {
  els = {};
  global.document = { getElementById: (id) => (els[id] ||= mkEl()) };
  global.window = { location: { origin: 'https://gw.example' } };
  global.WebSocket = { OPEN: 1 };
  global.navigator = { clipboard: { writeText: () => Promise.resolve() } };
});

function make(over = {}) {
  const sent = [];
  const socket = { readyState: 1, send: (s) => sent.push(JSON.parse(s)) };
  const confirms = [];
  const rooms = createRooms({
    getSocket: () => (over.socket === undefined ? socket : over.socket),
    getActiveView: () => (over.activeView === undefined ? { type: 'local_room', id: 'room-1' } : over.activeView),
    getRoomCreatorOf: () => over.roomCreatorOf ?? new Set(),
    getRoomInviteUrls: () => over.roomInviteUrls ?? {},
    // showConfirm: capture and auto-run the callback so the send path is exercised.
    showConfirm: (msg, onConfirm) => { confirms.push(msg); onConfirm(); },
    showCopyModal: () => {},
  });
  return { rooms, sent, confirms };
}

describe('requestRoomMembers', () => {
  it('asks the gateway for the member list', () => {
    const { rooms, sent } = make();
    rooms.requestRoomMembers('room-1');
    expect(sent).toContainEqual({ cmd: 'get_room_members', room_id: 'room-1' });
  });
});

describe('leaveRoom', () => {
  it('sends leave_local_room after confirmation', () => {
    const { rooms, sent } = make({ activeView: { type: 'local_room', id: 'room-1' } });
    rooms.leaveRoom();
    expect(sent).toContainEqual({ cmd: 'leave_local_room', room_id: 'room-1' });
  });
  it('does nothing when the active view is not a room', () => {
    const { rooms, sent } = make({ activeView: { type: 'dm', id: 'cert-1' } });
    rooms.leaveRoom();
    expect(sent).toHaveLength(0);
  });
});

describe('deleteRoom', () => {
  it('sends delete_room only when the user owns the room', () => {
    const { rooms, sent } = make({
      activeView: { type: 'local_room', id: 'room-1' },
      roomCreatorOf: new Set(['room-1']),
    });
    rooms.deleteRoom();
    expect(sent).toContainEqual({ cmd: 'delete_room', room_id: 'room-1' });
  });
  it('is a no-op for non-owners', () => {
    const { rooms, sent } = make({
      activeView: { type: 'local_room', id: 'room-1' },
      roomCreatorOf: new Set(),
    });
    rooms.deleteRoom();
    expect(sent).toHaveLength(0);
  });
});

describe('transferOwnership / kickMember', () => {
  it('transferOwnership sends to_did', () => {
    const { rooms, sent } = make();
    rooms.transferOwnership('room-1', 'did:key:zBob');
    expect(sent).toContainEqual({ cmd: 'transfer_ownership', room_id: 'room-1', to_did: 'did:key:zBob' });
  });
  it('kickMember sends after confirmation', () => {
    const { rooms, sent } = make();
    rooms.kickMember('room-1', 'did:key:zBob');
    expect(sent).toContainEqual({ cmd: 'kick_member', room_id: 'room-1', webid: 'did:key:zBob' });
  });
});

describe('copyRoomInvite', () => {
  it('populates the invite modal with url and extracted code', () => {
    const { rooms } = make({
      activeView: { type: 'local_room', id: 'room-1' },
      roomInviteUrls: { 'room-1': 'https://gw.example/?join=ABC123' },
    });
    rooms.copyRoomInvite();
    expect(els['invite-modal-url'].textContent).toBe('https://gw.example/?join=ABC123');
    expect(els['invite-modal-code'].textContent).toBe('ABC123');
    expect(els['room-invite-modal'].style.display).toBe('flex');
  });
});

describe('submitJoinRoom', () => {
  it('sends join_room with a bare code', () => {
    els['join-room-input'] = mkEl({ value: 'PLAINCODE' });
    const { rooms, sent } = make();
    rooms.submitJoinRoom();
    expect(sent).toContainEqual({ cmd: 'join_room', code: 'PLAINCODE' });
  });
  it('extracts the join code from a same-origin URL', () => {
    els['join-room-input'] = mkEl({ value: 'https://gw.example/?join=XYZ' });
    const { rooms, sent } = make();
    rooms.submitJoinRoom();
    expect(sent).toContainEqual({ cmd: 'join_room', code: 'XYZ' });
  });
});

describe('join double-submit guard', () => {
  it('sends join_room once while the first request is pending', () => {
    els['join-room-input'] = mkEl({ value: 'PLAINCODE' });
    els['join-room-submit-btn'] = mkEl({ textContent: 'Join', setAttribute() {}, removeAttribute() {} });
    const { rooms, sent } = make();
    rooms.submitJoinRoom();
    rooms.submitJoinRoom();
    expect(sent.filter((m) => m.cmd === 'join_room')).toHaveLength(1);
    expect(els['join-room-submit-btn'].disabled).toBe(true);
  });
});

describe('setButtonBusy / clearButtonBusy', () => {
  function btn() {
    const attrs = {};
    return {
      textContent: 'Create', disabled: false, attrs,
      setAttribute(k, v) { attrs[k] = v; }, removeAttribute(k) { delete attrs[k]; },
    };
  }
  it('disables, marks aria-busy and swaps the label, then restores', () => {
    const b = btn();
    setButtonBusy(b, 'Creating...');
    expect(b.disabled).toBe(true);
    expect(b.attrs['aria-busy']).toBe('true');
    expect(b.textContent).toBe('Creating...');
    expect(isButtonBusy(b)).toBe(true);
    clearButtonBusy(b);
    expect(b.disabled).toBe(false);
    expect(b.attrs['aria-busy']).toBeUndefined();
    expect(b.textContent).toBe('Create');
    expect(isButtonBusy(b)).toBe(false);
  });
  it('clears itself after the timeout when no reply arrives', () => {
    vi.useFakeTimers();
    try {
      const b = btn();
      setButtonBusy(b, 'Creating...', 15000);
      vi.advanceTimersByTime(14999);
      expect(b.disabled).toBe(true);
      vi.advanceTimersByTime(1);
      expect(b.disabled).toBe(false);
      expect(b.textContent).toBe('Create');
    } finally { vi.useRealTimers(); }
  });
});
