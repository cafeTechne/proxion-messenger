import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createProfile } from './profile.js';

let els;
function mkEl(over = {}) {
  return {
    style: {}, textContent: '', innerHTML: '', className: '', title: '',
    dataset: {}, onclick: null,
    classList: { add() {}, remove() {}, contains: () => false },
    getAttribute: () => null, querySelector: () => null, querySelectorAll: () => [],
    click: over.click, ...over,
  };
}
beforeEach(() => {
  els = {};
  global.document = {
    getElementById: (id) => {
      if (id in els) return els[id];
      // Sidebar nav entries only exist when a test sets them up.
      if (id.startsWith('nav-')) return null;
      return (els[id] = mkEl());
    },
  };
  global.window = { innerWidth: 1000, innerHeight: 800 };
  global.WebSocket = { OPEN: 1 };
});

function make(over = {}) {
  const sent = [];
  const socket = { readyState: 1, send: (s) => sent.push(JSON.parse(s)) };
  const userPresence = over.userPresence ?? {};
  const messageMap = over.messageMap ?? {};
  const profile = createProfile({
    getSocket: () => (over.socket === undefined ? socket : over.socket),
    showToast: () => {},
    getUserPresence: () => userPresence,
    getMessageMap: () => messageMap,
  });
  return { profile, sent, userPresence, messageMap };
}

describe('handlePresenceUpdate', () => {
  it('stores presence by webid', () => {
    const { profile, userPresence } = make();
    els['message-feed'] = mkEl({ querySelectorAll: () => [] });
    profile.handlePresenceUpdate({ webid: 'did:key:zBob', status: 'online', updated_at: 't' });
    expect(userPresence['did:key:zBob']).toEqual({ status: 'online', updated_at: 't' });
  });
  it('ignores events with no webid', () => {
    const { profile, userPresence } = make();
    profile.handlePresenceUpdate({ status: 'online' });
    expect(Object.keys(userPresence)).toHaveLength(0);
  });
});

describe('showProfileCard / hideProfileCard', () => {
  it('records the active webid and reveals the card', () => {
    const { profile } = make({ userPresence: { 'did:key:zBob': { status: 'online' } } });
    profile.showProfileCard('did:key:zBob', 'Bob', 50, 50);
    expect(profile.state.profileCardActive).toBe('did:key:zBob');
    expect(els['profile-name'].textContent).toBe('Bob');
  });
  it('hideProfileCard clears the active webid', () => {
    const { profile } = make();
    profile.state.profileCardActive = 'did:key:zBob';
    profile.hideProfileCard();
    expect(profile.state.profileCardActive).toBe(null);
  });
});

describe('profileCardOpenDM', () => {
  it('clicks an existing DM entry when present', () => {
    const click = vi.fn();
    const { profile } = make();
    profile.state.profileCardActive = 'did:key:zBob';
    const navId = 'nav-local-' + 'did:key:zBob'.replace(/[^a-zA-Z0-9]/g, '-');
    els[navId] = mkEl({ click });
    profile.profileCardOpenDM();
    expect(click).toHaveBeenCalled();
  });
  it('sends resolve_did when no DM entry exists', () => {
    const { profile, sent } = make();
    profile.state.profileCardActive = 'did:key:zCarol';
    profile.profileCardOpenDM();
    expect(sent).toContainEqual({ cmd: 'resolve_did', did: 'did:key:zCarol' });
  });
});
