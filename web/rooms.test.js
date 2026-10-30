import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createRooms, setButtonBusy, clearButtonBusy, isButtonBusy, inviteJoinCommand } from './rooms.js';

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
  it('offers a new link to the owner of a gateway room only', () => {
    class FakeWS { send(s) { this.sent.push(JSON.parse(s)); } }
    FakeWS.OPEN = 1;
    global.WebSocket = FakeWS;
    const ws = Object.assign(new FakeWS(), { readyState: 1, sent: [] });
    const urls = { 'room-1': 'https://gw.example/?join=ABC123' };
    const own = make({ socket: ws, roomCreatorOf: new Set(['room-1']), roomInviteUrls: urls });
    own.rooms.copyRoomInvite();
    expect(els['invite-modal-renew'].hidden).toBe(false);
    own.rooms.renewRoomInvite();
    expect(ws.sent).toContainEqual({ cmd: 'new_room_invite', room_id: 'room-1' });
    own.rooms.applyRenewedInvite({ room_id: 'room-1', code: 'NEW9', invite_url: 'https://gw.example/?join=NEW9' });
    expect(urls['room-1']).toBe('https://gw.example/?join=NEW9');
    expect(els['invite-modal-code'].textContent).toBe('NEW9');

    make({ socket: ws, roomInviteUrls: urls }).rooms.copyRoomInvite();
    expect(els['invite-modal-renew'].hidden).toBe(true);
    // A pod-only (browser build) socket has no gateway invite to replace.
    make({ roomCreatorOf: new Set(['room-1']), roomInviteUrls: urls }).rooms.copyRoomInvite();
    expect(els['invite-modal-renew'].hidden).toBe(true);
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
  it('joins a link to another gateway through this gateway, without leaving the page', () => {
    window.location.href = 'https://gw.example/';
    const link = 'https://other.example:8443/?join=REMOTE1';
    els['join-room-input'] = mkEl({ value: link });
    const { rooms, sent } = make();
    rooms.submitJoinRoom();
    expect(sent).toEqual([{ cmd: 'join_remote_room', url: link }]);
    expect(window.location.href).toBe('https://gw.example/');
  });
  it('explains a link with no join code', () => {
    els['join-room-input'] = mkEl({ value: 'https://other.example/' });
    els['join-room-error'] = mkEl();
    const { rooms, sent } = make();
    rooms.submitJoinRoom();
    expect(sent).toEqual([]);
    expect(els['join-room-error'].textContent).toBe('room.linkNoCode');
  });
});

describe('inviteJoinCommand', () => {
  const own = 'http://127.0.0.1:8080';
  it('maps input to the right gateway command', () => {
    expect(inviteJoinCommand('', own)).toBeNull();
    expect(inviteJoinCommand(' CODE ', own)).toEqual({ cmd: 'join_room', code: 'CODE' });
    expect(inviteJoinCommand(own + '/?join=A', own)).toEqual({ cmd: 'join_room', code: 'A' });
    expect(inviteJoinCommand('http://127.0.0.1:9090/?join=B', own))
      .toEqual({ cmd: 'join_remote_room', url: 'http://127.0.0.1:9090/?join=B' });
    expect(inviteJoinCommand('https://x.example/room', own)).toEqual({ error: 'room.linkNoCode' });
    expect(inviteJoinCommand('https://[bad', own)).toEqual({ error: 'room.invalidInviteLink' });
  });
});

describe('join double-submit guard', () => {
  it('sends join_room once while the first request is pending', () => {
    els['join-room-input'] = mkEl({ value: 'PLAINCODE' });
    const attrs = {};
    els['join-room-submit-btn'] = mkEl({
      textContent: 'Join', setAttribute(k, v) { attrs[k] = v; }, removeAttribute(k) { delete attrs[k]; },
    });
    const { rooms, sent } = make();
    rooms.submitJoinRoom();
    rooms.submitJoinRoom();
    expect(sent.filter((m) => m.cmd === 'join_room')).toHaveLength(1);
    expect(attrs['aria-disabled']).toBe('true');
  });
});

describe('setButtonBusy / clearButtonBusy', () => {
  function btn() {
    const attrs = {};
    const listeners = [];
    const b = {
      textContent: 'Create', disabled: false, attrs, listeners,
      setAttribute(k, v) { attrs[k] = v; }, removeAttribute(k) { delete attrs[k]; },
      addEventListener(type, fn, capture) { listeners.push({ type, fn, capture }); },
      // Dispatch a click through the registered listeners; returns the event.
      click() {
        const e = {
          currentTarget: b, defaultPrevented: false, stopped: false,
          preventDefault() { this.defaultPrevented = true; },
          stopImmediatePropagation() { this.stopped = true; },
        };
        listeners.filter((l) => l.type === 'click').forEach((l) => l.fn(e));
        return e;
      },
    };
    return b;
  }
  it('marks aria-disabled and aria-busy and swaps the label, then restores', () => {
    const b = btn();
    setButtonBusy(b, 'Creating...');
    // Never the disabled property: that would drop the user's focus to <body>.
    expect(b.disabled).toBe(false);
    expect(b.attrs['aria-disabled']).toBe('true');
    expect(b.attrs['aria-busy']).toBe('true');
    expect(b.textContent).toBe('Creating...');
    expect(isButtonBusy(b)).toBe(true);
    clearButtonBusy(b);
    expect(b.attrs['aria-disabled']).toBeUndefined();
    expect(b.attrs['aria-busy']).toBeUndefined();
    expect(b.textContent).toBe('Create');
    expect(isButtonBusy(b)).toBe(false);
  });
  it('blocks clicks (and so implicit submit) only while busy', () => {
    const b = btn();
    setButtonBusy(b, 'Creating...');
    setButtonBusy(b, 'Creating...');
    expect(b.listeners.filter((l) => l.type === 'click')).toHaveLength(1);
    expect(b.listeners[0].capture).toBe(true);
    const busyClick = b.click();
    expect(busyClick.defaultPrevented).toBe(true);
    expect(busyClick.stopped).toBe(true);
    clearButtonBusy(b);
    const idleClick = b.click();
    expect(idleClick.defaultPrevented).toBe(false);
    expect(idleClick.stopped).toBe(false);
  });
  it('clears itself after the timeout when no reply arrives', () => {
    vi.useFakeTimers();
    try {
      const b = btn();
      setButtonBusy(b, 'Creating...', 15000);
      vi.advanceTimersByTime(14999);
      expect(b.attrs['aria-disabled']).toBe('true');
      vi.advanceTimersByTime(1);
      expect(b.attrs['aria-disabled']).toBeUndefined();
      expect(b.textContent).toBe('Create');
    } finally { vi.useRealTimers(); }
  });
});
