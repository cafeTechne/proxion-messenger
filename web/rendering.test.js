import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createRendering } from './rendering.js';

// Minimal DOM element stub supporting the operations rendering.js uses.
function mkEl(over = {}) {
  const el = {
    _children: [],
    className: '', id: '', innerHTML: '', textContent: '', title: '', value: '',
    scrollTop: 0, scrollHeight: 1000, clientHeight: 500,
    style: {}, dataset: {},
    classList: { _s: new Set(), add(c){this._s.add(c);}, contains(c){return this._s.has(c);} },
    setAttribute() {}, addEventListener() {},
    appendChild(c){ el._children.push(c); return c; },
    insertBefore(c){ el._children.unshift(c); return c; },
    querySelectorAll(){ return []; },
    querySelector(){ return null; },
    get firstElementChild(){ return el._children[0] || null; },
    nextElementSibling: null, nextSibling: null,
    ...over,
  };
  return el;
}

let els, sent, view, host;
beforeEach(() => {
  els = {};
  sent = [];
  view = { type: 'room', id: 'room-1' };
  host = { messageMap: {}, allMessages: [], userPresence: {} };
  global.WebSocket = { OPEN: 1 };
  global.document = {
    getElementById: (id) => (id in els ? els[id] : null),
    createElement: () => mkEl(),
  };
  global.localStorage = { getItem: () => null, setItem: () => {} };
});

function make(over = {}) {
  return createRendering({
    getActiveView: () => view,
    getSocket: () => ({ readyState: 1, send: (m) => sent.push(JSON.parse(m)) }),
    getSelfWebId: () => 'did:key:zSelf',
    getSelfPubHex: () => null,
    getCurrentDisappearMs: () => 0,
    getMessageMap: () => host.messageMap,
    getAllMessages: () => host.allMessages,
    getUserPresence: () => host.userPresence,
    renderReactions: vi.fn(),
    openCtxMenu: vi.fn(),
    sendUpdateLastRead: vi.fn(),
    renderWindow: 100, scrollBatch: 50,
    ...over,
  });
}

describe('_buildThreadedMessages', () => {
  it('orders replies immediately after their parent and assigns depth', () => {
    const r = make();
    const msgs = [
      { message_id: 'a' },
      { message_id: 'b', reply_to_id: 'a' },
      { message_id: 'c' },
      { message_id: 'b2', reply_to_id: 'b' },
    ];
    const out = r._buildThreadedMessages(msgs);
    expect(out.map(m => m.message_id)).toEqual(['a', 'b', 'b2', 'c']);
    expect(out.find(m => m.message_id === 'a')._threadDepth).toBe(0);
    expect(out.find(m => m.message_id === 'b')._threadDepth).toBe(1);
    expect(out.find(m => m.message_id === 'b2')._threadDepth).toBe(2);
    expect(out.find(m => m.message_id === 'c')._threadDepth).toBe(0);
  });
  it('treats a reply to an unknown parent as a root', () => {
    const r = make();
    const out = r._buildThreadedMessages([{ message_id: 'x', reply_to_id: 'gone' }]);
    expect(out.map(m => m.message_id)).toEqual(['x']);
    expect(out[0]._threadDepth).toBe(0);
  });
});

describe('_dateLabelForTimestamp', () => {
  it('labels today / yesterday / older', () => {
    const r = make();
    const now = new Date();
    const yest = new Date(now); yest.setDate(now.getDate() - 1);
    expect(r._dateLabelForTimestamp(now.toISOString())).toBe('Today');
    expect(r._dateLabelForTimestamp(yest.toISOString())).toBe('Yesterday');
    const old = r._dateLabelForTimestamp('2020-03-05T12:00:00Z');
    expect(old).not.toBe('Today');
    expect(old).not.toBe('Yesterday');
  });
});

describe('scrollToBottom', () => {
  it('scrolls, clears the unread counter, and sends a read update', () => {
    els['message-feed'] = mkEl({ scrollHeight: 2000 });
    els['scroll-bottom-btn'] = mkEl({ style: { display: 'block' } });
    const sendUpdateLastRead = vi.fn();
    const r = make({ sendUpdateLastRead });
    r.state._scrollBottomUnread = 5;
    r.scrollToBottom();
    expect(els['message-feed'].scrollTop).toBe(2000);
    expect(r.state._scrollBottomUnread).toBe(0);
    expect(els['scroll-bottom-btn'].style.display).toBe('none');
    expect(sendUpdateLastRead).toHaveBeenCalledWith('room-1');
  });
});

describe('renderMessage (buffer tracking)', () => {
  it('buffers a new message into allMessages + messageMap and renders reactions', () => {
    els['message-feed'] = mkEl({ scrollHeight: 500, clientHeight: 500 }); // at-bottom
    const renderReactions = vi.fn();
    const r = make({ renderReactions });
    r.renderMessage({ message_id: 'm1', thread_id: 'room-1', from_webid: 'did:key:zBob', content: 'hi', timestamp: new Date().toISOString() });
    expect(host.allMessages.map(m => m.message_id)).toEqual(['m1']);
    expect(host.messageMap['m1']).toBeTruthy();
    expect(renderReactions).toHaveBeenCalledWith('m1');
  });
  it('skips messages for a non-active thread', () => {
    els['message-feed'] = mkEl();
    const r = make();
    r.renderMessage({ message_id: 'm2', thread_id: 'other-room', from_webid: 'x' });
    expect(host.allMessages).toHaveLength(0);
  });
});
