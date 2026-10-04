import { describe, it, expect, vi, beforeEach } from 'vitest';
import en from './locales/en.json';

// Interpolate real English strings so assertions read like the UI.
vi.mock('./i18n.js', () => ({
  t: (key, params) => String(en[key] ?? key).replace(/\{(\w+)\}/g, (m, k) => (params && k in params ? String(params[k]) : m)),
}));

import { createTyping, typingText, shortWebId } from './typing.js';

let els, sent, socket, view;
function mkEl(over = {}) {
  return { innerText: '', addEventListener: vi.fn(), ...over };
}
beforeEach(() => {
  els = {};
  sent = [];
  socket = { send: (m) => sent.push(JSON.parse(m)) };
  view = { type: 'room', id: 'room-1' };
  global.document = { getElementById: (id) => (id in els ? els[id] : null) };
  vi.useFakeTimers();
});

function make() {
  return createTyping({ getSocket: () => socket, getActiveView: () => view });
}

describe('handleTyping / updateTypingDisplay', () => {
  it('shows the typist when the event matches the active view', () => {
    els['typing-indicator'] = mkEl();
    const t = make();
    t.handleTyping({ room_id: 'room-1', from_webid: 'did:key:zAlice' });
    expect(els['typing-indicator'].innerText).toContain('is typing');
    expect(t.state.typingUsers['did:key:zAlice']).toBeTypeOf('number');
  });
  it('ignores typing events for a different view', () => {
    els['typing-indicator'] = mkEl();
    const t = make();
    t.handleTyping({ room_id: 'other-room', from_webid: 'did:key:zAlice' });
    expect(els['typing-indicator'].innerText).toBe('');
    expect(t.state.typingUsers['did:key:zAlice']).toBeUndefined();
  });
  it('clears the indicator once a typist goes stale (>4s)', () => {
    els['typing-indicator'] = mkEl();
    const t = make();
    t.handleTyping({ cert_id: 'room-1', from_webid: 'did:key:zAlice' });
    view = { type: 'room', id: 'room-1' };
    vi.advanceTimersByTime(5000);
    t.updateTypingDisplay();
    expect(els['typing-indicator'].innerText).toBe('');
  });
  it('updateTypingDisplay is a no-op when the indicator element is absent', () => {
    const t = make();
    expect(() => t.updateTypingDisplay()).not.toThrow();
  });
});

describe('attach (outgoing typing)', () => {
  it('sends a throttled room "typing" command on input', () => {
    const input = mkEl();
    let handler;
    input.addEventListener = (ev, fn) => { if (ev === 'input') handler = fn; };
    const t = make();
    t.attach(input);
    handler();
    handler(); // throttled — should not send twice
    expect(sent).toEqual([{ cmd: 'typing', room_id: 'room-1' }]);
    vi.advanceTimersByTime(3000);
    handler();
    expect(sent).toHaveLength(2);
  });
  it('uses cert_id for DM views', () => {
    view = { type: 'dm', id: 'cert-9' };
    const input = mkEl();
    let handler;
    input.addEventListener = (ev, fn) => { if (ev === 'input') handler = fn; };
    const t = make();
    t.attach(input);
    handler();
    expect(sent[0]).toEqual({ cmd: 'typing', cert_id: 'cert-9' });
  });
  it('does not send when there is no active view', () => {
    view = null;
    const input = mkEl();
    let handler;
    input.addEventListener = (ev, fn) => { if (ev === 'input') handler = fn; };
    const t = make();
    t.attach(input);
    handler();
    expect(sent).toHaveLength(0);
  });
});

describe('typing text', () => {
  const names = { 'did:key:z6MkAlice': 'Alice', 'did:key:z6MkBob': 'Bob' };
  const resolve = (w) => names[w] || '';
  it('names a single typist', () => {
    expect(typingText(['did:key:z6MkAlice'], resolve)).toBe('Alice is typing…');
  });
  it('names two typists', () => {
    expect(typingText(['did:key:z6MkAlice', 'did:key:z6MkBob'], resolve)).toBe('Alice and Bob are typing…');
  });
  it('collapses three or more typists', () => {
    expect(typingText(['did:key:z6MkAlice', 'did:key:z6MkBob', 'did:key:z6MkCarol'], resolve))
      .toBe('Several people are typing…');
  });
  it('is empty with nobody typing', () => {
    expect(typingText([], resolve)).toBe('');
  });
  it('never shows the did:key: prefix for an unknown typist', () => {
    const s = typingText(['did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK'], () => '');
    expect(s).toBe('…ta2doK is typing…');
  });
  it('shortWebId uses the host for an https WebID', () => {
    expect(shortWebId('https://alice.example/profile/card#me')).toBe('alice.example');
  });
  it('createTyping uses the resolver and only rewrites changed text', () => {
    let writes = 0, text = '';
    els['typing-indicator'] = {
      get innerText() { return text; },
      set innerText(v) { writes++; text = v; },
    };
    const t = createTyping({ getSocket: () => socket, getActiveView: () => view, resolveName: resolve });
    t.handleTyping({ room_id: 'room-1', from_webid: 'did:key:z6MkAlice' });
    expect(text).toBe('Alice is typing…');
    const before = writes;
    t.updateTypingDisplay();
    t.updateTypingDisplay();
    expect(writes).toBe(before);
    t.handleTyping({ room_id: 'room-1', from_webid: 'did:key:z6MkBob' });
    expect(text).toBe('Alice and Bob are typing…');
  });
});
