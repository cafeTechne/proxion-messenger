import { describe, it, expect, beforeEach } from 'vitest';
import { createMute } from './mute.js';

let els, store;
function mkEl(over = {}) {
  return { style: {}, querySelector: () => null, ...over };
}
beforeEach(() => {
  els = {};
  global.document = { getElementById: (id) => (id in els ? els[id] : null) };
  store = {};
  global.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  };
});

function make(initial = []) {
  const mutedThreads = new Set(initial);
  const mute = createMute({ getMutedThreads: () => mutedThreads });
  return { mute, mutedThreads };
}

describe('muteThread', () => {
  it('adds the thread to the set and persists it', () => {
    const { mute, mutedThreads } = make();
    mute.muteThread('t1');
    expect(mutedThreads.has('t1')).toBe(true);
    expect(JSON.parse(store['proxion_muted_threads'])).toContain('t1');
  });
});

describe('unmuteThread', () => {
  it('removes the thread from the set and persists it', () => {
    const { mute, mutedThreads } = make(['t1', 't2']);
    mute.unmuteThread('t1');
    expect(mutedThreads.has('t1')).toBe(false);
    expect(JSON.parse(store['proxion_muted_threads'])).toEqual(['t2']);
  });
});

describe('_rerenderMuteIcon', () => {
  it('shows the mute icon and hides the badge for a muted thread', () => {
    const badge = mkEl();
    const icon = mkEl();
    els['nav-t1'] = mkEl({ querySelector: (sel) => (sel === '.badge' ? badge : sel === '.mute-icon' ? icon : null) });
    const { mute } = make(['t1']);
    mute._rerenderMuteIcon('t1');
    expect(badge.style.display).toBe('none');
    expect(icon.style.display).toBe('');
  });
  it('is a no-op when the nav element is missing', () => {
    const { mute } = make();
    expect(() => mute._rerenderMuteIcon('nope')).not.toThrow();
  });
});
