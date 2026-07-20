import { describe, it, expect, beforeEach } from 'vitest';
import { createReactions } from './reactions.js';

// DOM stub: getElementById returns a fake element; createElement a fake span.
beforeEach(() => {
  const mkEl = () => ({
    style: {}, innerHTML: '', innerText: '', className: '', onclick: null,
    offsetWidth: 160, offsetHeight: 120, contains: () => false,
    appendChild() {}, classList: { contains: () => false },
  });
  global.document = {
    getElementById: () => mkEl(),
    createElement: () => mkEl(),
  };
  global.window = { innerWidth: 1000 };
});

function make(over = {}) {
  const sent = [];
  const socket = { readyState: 1, send: (s) => sent.push(JSON.parse(s)) };
  const messageReactions = over.messageReactions ?? {};
  const r = createReactions({
    getSocket: () => (over.socket === undefined ? socket : over.socket),
    getActiveView: () => (over.activeView === undefined ? { type: 'local_room', id: 'room-1' } : over.activeView),
    getSelfWebId: () => over.selfWebId ?? 'did:key:zSelf',
    getMessageReactions: () => messageReactions,
  });
  return { r, sent, messageReactions };
}

describe('handleReactionEvent', () => {
  it('adds a reactor, de-duplicating repeats', () => {
    const { r, messageReactions } = make();
    const ev = { message_id: 'm1', emoji: '👍', from_webid: 'did:key:zBob' };
    r.handleReactionEvent(ev, 'add');
    r.handleReactionEvent(ev, 'add'); // duplicate
    expect(messageReactions['m1']['👍']).toEqual(['did:key:zBob']);
  });
  it('removes a reactor', () => {
    const { r, messageReactions } = make({
      messageReactions: { m1: { '👍': ['did:key:zBob', 'did:key:zCarol'] } },
    });
    r.handleReactionEvent({ message_id: 'm1', emoji: '👍', from_webid: 'did:key:zBob' }, 'remove');
    expect(messageReactions['m1']['👍']).toEqual(['did:key:zCarol']);
  });
});

describe('renderReactions animation (D3)', () => {
  function capture() {
    const spans = [];
    const container = { innerHTML: '', appendChild: (s) => spans.push(s) };
    global.document = {
      getElementById: (id) => (id === 'reactions-m1' ? container : null),
      createElement: () => ({ style: {}, className: '', innerText: '', onclick: null }),
    };
    return spans;
  }
  it('adds reaction-anim only to the just-added emoji pill', () => {
    const spans = capture();
    const { r } = make({ messageReactions: { m1: { '👍': ['did:key:zBob'], '🎉': ['did:key:zCarol'] } } });
    r.renderReactions('m1', '👍');
    expect(spans.find(s => s.innerText.startsWith('👍')).className).toContain('reaction-anim');
    expect(spans.find(s => s.innerText.startsWith('🎉')).className).not.toContain('reaction-anim');
  });
  it('does not animate on a plain render (e.g. initial message render)', () => {
    const spans = capture();
    const { r } = make({ messageReactions: { m1: { '👍': ['did:key:zBob'] } } });
    r.renderReactions('m1');
    expect(spans[0].className).not.toContain('reaction-anim');
  });
});

describe('addEmoji', () => {
  it('routes to room_id for a room view', () => {
    const { r, sent } = make({ activeView: { type: 'local_room', id: 'room-1' } });
    r.addEmoji('🎉', 'm1');
    expect(sent).toContainEqual({ cmd: 'add_reaction', message_id: 'm1', emoji: '🎉', room_id: 'room-1' });
  });
  it('routes to cert_id for a DM view and falls back to lastEmojiMsgId', () => {
    const { r, sent } = make({ activeView: { type: 'dm', id: 'cert-9' } });
    r.state.lastEmojiMsgId = 'm-last';
    r.addEmoji('🔥'); // no msgId → uses state.lastEmojiMsgId
    expect(sent).toContainEqual({ cmd: 'add_reaction', message_id: 'm-last', emoji: '🔥', cert_id: 'cert-9' });
  });
});

describe('removeReaction', () => {
  it('sends remove_reaction with the right target', () => {
    const { r, sent } = make({ activeView: { type: 'local_room', id: 'room-1' } });
    r.removeReaction('👍', 'm1');
    expect(sent).toContainEqual({ cmd: 'remove_reaction', message_id: 'm1', emoji: '👍', room_id: 'room-1' });
  });
  it('is a no-op without a socket', () => {
    const { r, sent } = make({ socket: null });
    r.removeReaction('👍', 'm1');
    expect(sent).toHaveLength(0);
  });
});

describe('togglePicker', () => {
  it('records the active message id in state', () => {
    const { r } = make();
    r.togglePicker('m1', 100, 200);
    expect(r.state.lastEmojiMsgId).toBe('m1');
  });
});

describe('custom-emoji reaction pills (R60A)', () => {
  it('renders a :name: key as an image pill when the room map has it', () => {
    const container = {
      style: {}, innerHTML: '', children: [],
      appendChild(c) { this.children.push(c); },
    };
    const made = [];
    global.document = {
      getElementById: (id) => (id.startsWith('reactions-') ? container : null),
      createElement: (tag) => {
        const el = { tag, style: {}, className: '', innerText: '', src: '', alt: '',
          children: [], appendChild(c) { this.children.push(c); }, setAttribute() {} };
        made.push(el);
        return el;
      },
      createTextNode: (t) => ({ text: t }),
    };
    const r = createReactions({
      getSocket: () => null,
      getActiveView: () => ({ type: 'local_room', id: 'room-1' }),
      getSelfWebId: () => 'did:key:zSelf',
      getMessageReactions: () => ({ m1: { ':blob:': ['did:key:zBob'] } }),
      getRoomEmojiMap: () => ({ blob: { mime: 'image/png', data_b64: 'QUJD' } }),
    });
    r.renderReactions('m1');
    const img = made.find(el => el.tag === 'img');
    expect(img).toBeTruthy();
    expect(img.src).toBe('data:image/png;base64,QUJD');
    expect(img.alt).toBe(':blob:');
  });
  it('unknown :name: keys fall back to literal text', () => {
    const container = { style: {}, innerHTML: '', children: [], appendChild(c) { this.children.push(c); } };
    global.document = {
      getElementById: () => container,
      createElement: (tag) => ({ tag, style: {}, children: [], appendChild(c) { this.children.push(c); } }),
      createTextNode: (t) => ({ text: t }),
    };
    const r = createReactions({
      getSocket: () => null,
      getActiveView: () => ({ type: 'local_room', id: 'room-1' }),
      getSelfWebId: () => 'did:key:zSelf',
      getMessageReactions: () => ({ m1: { ':nope:': ['did:key:zBob'] } }),
      getRoomEmojiMap: () => ({}),
    });
    r.renderReactions('m1');
    expect(container.children[0].innerText).toBe(':nope: 1');
  });
});
