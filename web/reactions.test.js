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
