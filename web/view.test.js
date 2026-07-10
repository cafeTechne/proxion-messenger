import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('./pod.js', () => ({ podWriteReadState: vi.fn(() => Promise.resolve()) }));

import { createView } from './view.js';

let els, host, sent, calls;
function mkEl(over = {}) {
  const el = {
    _children: [], id: '', className: '', title: '', innerHTML: '', innerText: '', textContent: '',
    style: {}, dataset: {},
    classList: { _s: new Set(), add(c){this._s.add(c);}, remove(c){this._s.delete(c);}, contains(c){return this._s.has(c);} },
    setAttribute(k, v){ this.dataset[k] = v; },
    addEventListener() {}, click() { if (this.onclick) this.onclick(); },
    appendChild(c){ this._children.push(c); return c; },
    remove() {},
    querySelectorAll(){ return []; }, querySelector(){ return null; },
    onclick: null,
    ...over,
  };
  return el;
}

beforeEach(() => {
  els = {};
  sent = [];
  calls = {};
  host = {
    socket: { readyState: 1, send: (m) => sent.push(JSON.parse(m)) },
    activeView: null, messageMap: { stale: 1 }, allMessages: [{ thread_id: 'x' }],
    currentRoomMembers: ['old'],
    peerDidToCertId: { stale: 'x' }, threadNames: {}, roomInviteUrls: {},
    roomCreatorOf: new Set(['room-own']), unreadCounts: {}, mutedThreads: new Set(),
  };
  global.WebSocket = { OPEN: 1 };
  global.window = { innerWidth: 1200 };
  global.localStorage = { getItem: () => null, setItem: () => {} };
  global.CSS = { escape: (s) => s };
  global.document = {
    getElementById: (id) => (id in els ? els[id] : null),
    createElement: () => mkEl(),
    querySelectorAll: () => [],
  };
  // common elements
  ['chat-header-name','message-feed','members-toggle','leave-room-btn','delete-room-btn',
   'members-panel','start-call-btn','invite-btn','contacts-list','contacts-section',
   'room-list','disappear-timer-select','integrations-btn','room-list-empty-hint']
    .forEach(id => { els[id] = mkEl(); });
});

function spy(name) { return vi.fn((...a) => { (calls[name] = calls[name] || []).push(a); }); }

function make() {
  return createView({
    getSocket: () => host.socket,
    setActiveView: (v) => { host.activeView = v; },
    setMessageMap: (m) => { host.messageMap = m; },
    setAllMessages: (a) => { host.allMessages = a; },
    setCurrentRoomMembers: (c) => { host.currentRoomMembers = c; },
    getAllMessages: () => host.allMessages,
    getPeerDidToCertId: () => host.peerDidToCertId, getThreadNames: () => host.threadNames,
    getRoomInviteUrls: () => host.roomInviteUrls, getRoomCreatorOf: () => host.roomCreatorOf,
    getUnreadCounts: () => host.unreadCounts, getMutedThreads: () => host.mutedThreads,
    hideEmptyState: spy('hideEmptyState'), updateE2EStatus: spy('updateE2EStatus'),
    updateIdentityFingerprint: spy('updateIdentityFingerprint'), closeMentionDropdown: spy('closeMentionDropdown'),
    updateSidebarBadge: spy('updateSidebarBadge'), sendUpdateLastRead: spy('sendUpdateLastRead'),
    loadRoomHistory: spy('loadRoomHistory'), toggleSidebar: spy('toggleSidebar'),
    updateDisappearBanner: spy('updateDisappearBanner'), requestRoomMembers: spy('requestRoomMembers'),
    renderMembersPanel: spy('renderMembersPanel'), updateVoiceChannels: spy('updateVoiceChannels'),
    openSidebarCtx: spy('openSidebarCtx'), resetDateDivider: spy('resetDateDivider'),
  });
}

describe('openContactThread', () => {
  it('sets a dm view, resets message state, and fetches history', () => {
    make().openContactThread({ certificate_id: 'cert-9', peer_did: 'did:key:zBob', display_name: 'Bob' });
    expect(host.activeView).toMatchObject({ type: 'dm', id: 'cert-9', certId: 'cert-9', peerDid: 'did:key:zBob', local: false });
    expect(host.messageMap).toEqual({});       // reset
    expect(host.allMessages).toEqual([]);       // reset
    expect(calls.resetDateDivider).toBeTruthy();
    expect(sent).toEqual([
      { cmd: 'read_dm', cert_id: 'cert-9' },
      { cmd: 'mark_read', thread_id: 'cert-9' },
      { cmd: 'get_peer_device_keys', peer_webid: 'did:key:zBob' },
    ]);
    expect(host.unreadCounts['cert-9']).toBe(0);
    expect(calls.updateE2EStatus[0]).toEqual(['did:key:zBob']);
  });
});

describe('openLocalDmThread', () => {
  it('sets a local_dm view, resets members, marks read, loads history', () => {
    make().openLocalDmThread('dm-1', 'Carol', 'did:key:zCarol');
    expect(host.activeView).toMatchObject({ type: 'local_dm', id: 'dm-1', local: true, peerWebid: 'did:key:zCarol' });
    expect(host.currentRoomMembers).toEqual([]);
    expect(sent).toContainEqual({ cmd: 'mark_read', thread_id: 'dm-1' });
    expect(calls.sendUpdateLastRead[0]).toEqual(['dm-1']);
    expect(calls.loadRoomHistory[0]).toEqual(['dm-1']);
  });
});

describe('renderContacts', () => {
  it('resets peerDidToCertId in place and rebuilds the map + list', () => {
    const original = host.peerDidToCertId;
    make().renderContacts([{ certificate_id: 'c1', peer_did: 'did:key:zA', display_name: 'A' }]);
    expect(host.peerDidToCertId).toBe(original);          // same object (mutated in place)
    expect(host.peerDidToCertId).toEqual({ 'did:key:zA': 'c1' });
    expect(host.peerDidToCertId.stale).toBeUndefined();   // cleared
    expect(els['contacts-list']._children).toHaveLength(1);
  });
  it('hides the section when there are no contacts', () => {
    els['contacts-section'] = mkEl({ style: { display: '' } });
    make().renderContacts([]);
    expect(els['contacts-section'].style.display).toBe('none');
  });
});

describe('addRoomToSidebar + its click', () => {
  it('adds a room li and, on click, opens a local_room view', () => {
    const v = make();
    v.addRoomToSidebar('room-own', 'My Room', 'https://gw/?join=AB');
    expect(host.roomInviteUrls['room-own']).toBe('https://gw/?join=AB');
    const li = els['room-list']._children[0];
    expect(li).toBeTruthy();
    li.onclick();
    expect(host.activeView).toMatchObject({ type: 'local_room', id: 'room-own', local: true });
    expect(sent).toContainEqual({ cmd: 'mark_read', thread_id: 'room-own' });
    expect(calls.loadRoomHistory[0]).toEqual(['room-own', 100]);
    expect(calls.requestRoomMembers[0]).toEqual(['room-own']);
  });
  it('does not re-add an existing room', () => {
    els['nav-dup'] = mkEl();
    const v = make();
    v.addRoomToSidebar('dup', 'Dup', '');
    expect(els['room-list']._children).toHaveLength(0);
  });
});

describe('populateSidebar + its click', () => {
  it('builds room items whose click sends read_room and updates voice channels', () => {
    const v = make();
    v.populateSidebar('room-list', [{ id: 'r9', name: 'Nine' }], 'room');
    const li = els['room-list']._children[0];
    li.onclick();
    expect(host.activeView).toMatchObject({ type: 'room', id: 'r9', name: 'Nine' });
    expect(sent).toContainEqual({ cmd: 'read_room', room_id: 'r9' });
    expect(calls.updateVoiceChannels[0]).toEqual(['r9']);
  });

  it('renders an actionable CTA when the list is empty (G3/G4)', () => {
    const v = make();
    const ctaBtn = mkEl();
    const createRoomClick = vi.fn();
    els['create-room-btn'] = mkEl({ onclick: createRoomClick });
    let li;
    global.document.createElement = () => (li = mkEl({ querySelector: () => ctaBtn }));
    v.populateSidebar('room-list', [], 'room');
    expect(li.className).toBe('sidebar-empty');
    expect(li.innerHTML).toContain('sidebar.empty.createRoom');
    ctaBtn.onclick();                       // user taps the CTA
    expect(createRoomClick).toHaveBeenCalled();
  });

  it('renders no CTA for lists without an empty-state mapping', () => {
    const v = make();
    els['voice-list'] = mkEl();
    v.populateSidebar('voice-list', [], 'room');
    expect(els['voice-list']._children.length).toBe(0);
  });
});

describe('_navigateToThread', () => {
  it('clicks the matching nav element', () => {
    const clicked = vi.fn();
    els['nav-t1'] = mkEl({ onclick: clicked });
    make()._navigateToThread('t1');
    expect(clicked).toHaveBeenCalled();
  });
  it('is a no-op for a falsy id', () => {
    expect(() => make()._navigateToThread('')).not.toThrow();
  });
});
