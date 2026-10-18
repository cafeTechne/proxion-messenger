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
    getMessages: () => over.messages || [],
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
  it('shows the undecryptable notice for a sealed DM pin, never its text', () => {
    const { pins } = make({ activeView: { type: 'local_dm', id: 'cert-9' } });
    const appended = [];
    document.getElementById('pin-list').appendChild = (el) => appended.push(el);
    pins.renderPins([{ message_id: 'm1', pinned_by: 'x', content: '[could not decrypt]' }]);
    expect(appended[0].innerHTML).toContain('e2e.undecryptable');
    expect(appended[0].innerHTML).not.toContain('[could not decrypt]');
  });
  it('shows an empty-state message when there are no pins', () => {
    const { pins } = make();
    pins.renderPins([]);
    expect(document.getElementById('pin-list').innerHTML).toContain('pin.noneP');
    expect(document.getElementById('pin-list').innerHTML).toContain('pin.noneHint');
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
  it('names the pinned message author (escaped), not the raw pinner id', () => {
    const { pins } = make({ messages: [
      { message_id: 'm1', from_webid: 'did:key:z6MkAAAAAbcdef', from_display_name: '<b>Sam</b>' },
    ] });
    const appended = [];
    document.getElementById('pin-list').appendChild = (el) => appended.push(el);
    pins.renderPins([{ message_id: 'm1', pinned_by: 'did:key:z6MkPINNER', content: 'ok' }]);
    const html = appended[0].innerHTML;
    expect(html).toContain('&lt;b&gt;Sam&lt;/b&gt;');
    expect(html).not.toContain('<b>Sam');
    expect(html).not.toContain('z6MkPINNER');
  });
  it('omits the author line when the message is not loaded', () => {
    const { pins } = make();
    const appended = [];
    document.getElementById('pin-list').appendChild = (el) => appended.push(el);
    pins.renderPins([{ message_id: 'm9', pinned_by: '<svg/onload=alert(1)>', content: 'ok' }]);
    const html = appended[0].innerHTML;
    expect(html).not.toContain('ref-row__meta');
    expect(html).not.toContain('<svg');
  });
  it('escapes a malicious pin content preview', () => {
    const { pins } = make();
    const appended = [];
    document.getElementById('pin-list').appendChild = (el) => appended.push(el);
    pins.renderPins([
      { message_id: 'm1', pinned_by: 'Bob', content: '<img src=x onerror=alert(1)>' },
    ]);
    const html = appended[0].innerHTML;
    expect(html).not.toContain('<img src=x onerror=alert(1)>');
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
  });
  it('escapes a malicious message_id so it cannot break out of the button attributes', () => {
    const { pins } = make();
    const appended = [];
    document.getElementById('pin-list').appendChild = (el) => appended.push(el);
    pins.renderPins([
      { message_id: '"><img src=x onerror=alert(1)>', pinned_by: 'Bob', content: 'ok' },
    ]);
    const html = appended[0].innerHTML;
    // message_id is client-supplied and stored verbatim; the attribute-breakout
    // sequence must not survive into the data-msg-id attributes.
    expect(html).not.toContain('"><img src=x onerror=alert(1)>');
    expect(html).toContain('data-msg-id="&quot;&gt;&lt;img');
  });
});
