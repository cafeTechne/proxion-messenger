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
    openContactByDid: over.openContactByDid,
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
  it("opens a friend's contact thread instead of a second chat", () => {
    const openContactByDid = vi.fn(() => true);
    const click = vi.fn();
    const { profile, sent } = make({ openContactByDid });
    profile.state.profileCardActive = 'did:key:zBob';
    els['nav-local-did-key-zBob'] = mkEl({ click });
    profile.profileCardOpenDM();
    expect(openContactByDid).toHaveBeenCalledWith('did:key:zBob');
    expect(click).not.toHaveBeenCalled();
    expect(sent).toEqual([]);
  });
  it('sends resolve_did when no DM entry exists', () => {
    const { profile, sent } = make();
    profile.state.profileCardActive = 'did:key:zCarol';
    profile.profileCardOpenDM();
    expect(sent).toContainEqual({ cmd: 'resolve_did', did: 'did:key:zCarol' });
  });
});

describe('contact profile panel', () => {
  const DID = 'did:key:z6MkovEzDthDmJ395QGQkkykpLwcBHVMX9vyKEF5AGLXN6TH';
  function mk(opts = {}) {
    global.fetch = vi.fn(() => Promise.resolve({ ok: false }));
    return createProfile({
      getSocket: () => null, showToast: () => {},
      getUserPresence: () => opts.presence ?? {}, getMessageMap: () => ({}),
      isSelf: (w) => w === opts.self, isContact: (w) => w === opts.contact,
    });
  }
  it('uses the name the caller shows and live presence, not the DID', () => {
    mk({ presence: { [DID]: { status: 'online' } } }).showContactProfile(DID, 'Sam Okafor');
    expect(els['contact-profile-name'].textContent).toBe('Sam Okafor');
    expect(els['contact-profile-avatar'].textContent).toBe('S');
    expect(els['contact-profile-status'].innerHTML).toContain('online');
  });
  it('falls back to the distinguishing end of the DID', () => {
    mk().showContactProfile(DID);
    expect(els['contact-profile-name'].textContent).toBe('did:key:…' + DID.slice(-10));
  });
  it('hides empty gateway and fingerprint rows', () => {
    mk().showContactProfile(DID, 'Sam');
    expect(els['contact-profile-gateway'].hidden).toBe(true);
    expect(els['contact-profile-gateway-label'].hidden).toBe(true);
    expect(els['contact-profile-fingerprint-label'].hidden).toBe(true);
  });
  it('offers no DM or removal on your own profile', () => {
    mk({ self: DID, contact: DID }).showContactProfile(DID, 'Me');
    expect(els['contact-profile-dm-btn'].hidden).toBe(true);
    expect(els['contact-profile-remove-btn'].hidden).toBe(true);
  });
  it('offers removal only for an actual contact', () => {
    mk({ contact: 'did:key:zOther' }).showContactProfile(DID, 'Stranger');
    expect(els['contact-profile-dm-btn'].hidden).toBe(false);
    expect(els['contact-profile-remove-btn'].hidden).toBe(true);
    mk({ contact: DID }).showContactProfile(DID, 'Friend');
    expect(els['contact-profile-remove-btn'].hidden).toBe(false);
  });
});

describe('profile popover on your own card', () => {
  it('hides Send DM and Block', () => {
    const actions = mkEl(), divider = mkEl();
    els['profile-card'] = mkEl({ querySelector: (s) => (s === '.profile-actions' ? actions : s === '.profile-divider' ? divider : null) });
    const p = createProfile({ getSocket: () => null, showToast() {}, getUserPresence: () => ({}), getMessageMap: () => ({}), isSelf: (w) => w === 'did:key:zMe' });
    p.showProfileCard('did:key:zMe', 'Me', 10, 10);
    expect(actions.hidden).toBe(true);
    p.showProfileCard('did:key:zYou', 'You', 10, 10);
    expect(actions.hidden).toBe(false);
    expect(divider.hidden).toBe(false);
  });
});
