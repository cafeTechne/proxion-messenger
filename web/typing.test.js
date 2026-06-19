import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createTyping } from './typing.js';

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
