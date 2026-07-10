import { describe, it, expect, beforeEach } from 'vitest';
import { createPins } from './pins.js';

beforeEach(() => {
  const mkEl = () => ({ style: {}, innerHTML: '', appendChild() {}, scrollIntoView() {} });
  const els = {};
  global.document = {
    getElementById: (id) => (els[id] ||= mkEl()),
    createElement: () => mkEl(),
  };
  global.setTimeout = (fn) => fn && 0;
});

function make(over = {}) {
  const sent = [];
  const socket = { readyState: 1, send: (s) => sent.push(JSON.parse(s)) };
  const pins = createPins({
    getSocket: () => (over.socket === undefined ? socket : over.socket),
    getActiveView: () => (over.activeView === undefined ? { type: 'local_room', id: 'room-1' } : over.activeView),
  });
  return { pins, sent };
}

describe('pinMsg', () => {
  it('sends pin_message with a room: thread id', () => {
    const { pins, sent } = make({ activeView: { type: 'local_room', id: 'room-1' } });
    pins.pinMsg('m1');
    expect(sent).toContainEqual({ cmd: 'pin_message', message_id: 'm1', thread_id: 'room:room-1' });
  });
  it('uses a dm: thread id for DM views', () => {
    const { pins, sent } = make({ activeView: { type: 'local_dm', id: 'cert-9' } });
    pins.pinMsg('m1');
    expect(sent).toContainEqual({ cmd: 'pin_message', message_id: 'm1', thread_id: 'dm:cert-9' });
  });
  it('is a no-op without a socket', () => {
    const { pins, sent } = make({ socket: null });
    pins.pinMsg('m1');
    expect(sent).toHaveLength(0);
  });
});

describe('showPinPanel', () => {
  it('requests pins and opens the panel', () => {
    const { pins, sent } = make({ activeView: { type: 'local_room', id: 'room-1' } });
    pins.showPinPanel();
    expect(sent).toContainEqual({ cmd: 'get_pins', thread_id: 'room:room-1' });
    expect(document.getElementById('pin-panel').style.display).toBe('block');
  });
});

describe('unpinMsg', () => {
  it('sends unpin_message with the given thread id', () => {
    const { pins, sent } = make();
    pins.unpinMsg('m1', 'room:room-1');
    expect(sent).toContainEqual({ cmd: 'unpin_message', message_id: 'm1', thread_id: 'room:room-1' });
  });
});

describe('renderPins', () => {
  it('shows an empty-state message when there are no pins', () => {
    const { pins } = make();
    pins.renderPins([]);
    expect(document.getElementById('pin-list').innerHTML).toContain('pin.noneP');
  });
  it('renders a row per pin', () => {
    const { pins } = make();
    const appended = [];
    document.getElementById('pin-list').appendChild = (el) => appended.push(el);
    pins.renderPins([
      { message_id: 'm1', pinned_by: 'Bob', content: 'hello' },
      { message_id: 'm2', pinned_by: 'Carol', content: 'world' },
    ]);
    expect(appended).toHaveLength(2);
  });
});
