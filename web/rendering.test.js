import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createRendering, captureScrollAnchor, restoreScrollAnchor, isFeedAtBottom } from './rendering.js';

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
    getRoomCode: () => 'CODE',
    renderWindow: 100, scrollBatch: 50,
    ...over,
  });
}

describe('mergeOlderHistory (C3 federated pagination)', () => {
  it('prepends deduped older messages in chronological order and re-renders', () => {
    els['message-feed'] = mkEl();
    host.allMessages = [{ message_id: 'c', timestamp: '2026-01-03' }];
    host.messageMap = { c: host.allMessages[0] };
    const r = make();
    const added = r.mergeOlderHistory([
      { message_id: 'a', timestamp: '2026-01-01' },
      { message_id: 'b', timestamp: '2026-01-02' },
      { message_id: 'c', timestamp: '2026-01-03' }, // duplicate — ignored
    ]);
    expect(added).toBe(2);
    expect(host.allMessages.map(m => m.message_id)).toEqual(['a', 'b', 'c']); // chronological
    expect(host.messageMap.a).toBeTruthy();
    // No anchor in the stub feed, so it falls back to keeping the distance from
    // the bottom of the content (the old code jumped to scrollTop = 10).
    expect(els['message-feed'].scrollTop).toBe(0);
  });
  it('returns 0 and no-ops when every message is already present', () => {
    els['message-feed'] = mkEl();
    host.allMessages = [{ message_id: 'a', timestamp: '2026-01-01' }];
    host.messageMap = { a: host.allMessages[0] };
    const r = make();
    expect(r.mergeOlderHistory([{ message_id: 'a', timestamp: '2026-01-01' }])).toBe(0);
    expect(host.allMessages).toHaveLength(1);
  });
});

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
    expect(r._dateLabelForTimestamp(now.toISOString())).toBe('time.today');
    expect(r._dateLabelForTimestamp(yest.toISOString())).toBe('time.yesterday');
    const old = r._dateLabelForTimestamp('2020-03-05T12:00:00Z');
    expect(old).not.toBe('time.today');
    expect(old).not.toBe('time.yesterday');
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

describe('avatar XSS hardening', () => {
  it('sanitizes a malicious avatar_b64 so it cannot break out of the src attribute', () => {
    els['message-feed'] = mkEl({ scrollHeight: 500, clientHeight: 500 }); // at-bottom
    const created = [];
    const orig = global.document.createElement;
    global.document.createElement = () => { const el = mkEl(); created.push(el); return el; };
    try {
      const r = make();
      r.renderMessage({
        message_id: 'x1', thread_id: 'room-1', from_webid: 'did:key:zBob',
        from_avatar_b64: 'AAAA" onerror="alert(document.cookie)',
        content: 'hi', timestamp: new Date().toISOString(),
      });
      const html = created.map(e => e.innerHTML).join('');
      // Avatar is still rendered as a data URL, but the attribute-breakout
      // sequence (a quote closing src, then an onerror handler) is gone: the
      // leftover "onerror" letters are inert base64 text inside the src value.
      expect(html).toContain('src="data:image/png;base64,');
      expect(html).not.toContain('" onerror');
      expect(html).not.toContain('AAAA" onerror');
    } finally {
      global.document.createElement = orig;
    }
  });
});

describe('data-name attribute hardening', () => {
  it('escapes a malicious display name in the avatar data-name attribute', () => {
    els['message-feed'] = mkEl({ scrollHeight: 500, clientHeight: 500 });
    const created = [];
    const orig = global.document.createElement;
    global.document.createElement = () => { const el = mkEl(); created.push(el); return el; };
    try {
      const r = make();
      r.renderMessage({
        message_id: 'n1', thread_id: 'room-1', from_webid: 'did:key:zBob',
        from_display_name: '"><img src=x onerror=alert(1)>',
        content: 'hi', timestamp: new Date().toISOString(),
      });
      const html = created.map(e => e.innerHTML).join('');
      expect(html).toContain('data-name="');
      // The attribute-breakout sequence is neutralized: no raw quote+tag.
      expect(html).not.toContain('"><img src=x onerror=alert(1)>');
      expect(html).toContain('&quot;&gt;&lt;img');
    } finally {
      global.document.createElement = orig;
    }
  });
});

describe('message id XSS hardening', () => {
  it('escapes a malicious message_id everywhere it lands in an innerHTML attribute', () => {
    els['message-feed'] = mkEl({ scrollHeight: 500, clientHeight: 500 }); // at-bottom
    const created = [];
    const orig = global.document.createElement;
    global.document.createElement = () => { const el = mkEl(); created.push(el); return el; };
    try {
      const r = make();
      // Own message so the edit/delete/receipt attribute paths also render.
      r.renderMessage({
        message_id: '"><img src=x onerror=alert(1)>', thread_id: 'room-1',
        from_webid: 'did:key:zSelf', local: true,
        content: 'hi', timestamp: new Date().toISOString(),
      });
      const html = created.map(e => e.innerHTML).join('');
      // No attacker markup is injected by the id, and the reactions container +
      // action-button data-msg-id attributes carry only the escaped form.
      expect(html).not.toContain('"><img src=x onerror=alert(1)>');
      expect(html).toContain('data-msg-id="&quot;&gt;&lt;img');
      expect(html).toContain('id="reactions-&quot;&gt;&lt;img');
    } finally {
      global.document.createElement = orig;
    }
  });
  it('escapes a malicious reply_to_id in the reply-context attribute', () => {
    const feed = mkEl();
    const created = [];
    const orig = global.document.createElement;
    global.document.createElement = () => { const el = mkEl(); created.push(el); return el; };
    try {
      const r = make();
      const mal = '"><img src=x onerror=alert(1)>';
      host.messageMap[mal] = { from_display_name: 'Parent', content: 'parent body' };
      r._renderMessageEl({
        message_id: 'child', reply_to_id: mal, from_webid: 'did:key:zBob',
        content: 'reply', timestamp: new Date().toISOString(),
      }, feed, null);
      const html = created.map(e => e.innerHTML).join('');
      expect(html).not.toContain('"><img src=x onerror=alert(1)>');
      expect(html).toContain('data-reply-id="&quot;&gt;&lt;img');
    } finally {
      global.document.createElement = orig;
    }
  });
});

describe('attachmentKind (R59A pure)', () => {
  it('classifies image, video, audio, and unknown mimes', async () => {
    const { attachmentKind } = await import('./rendering.js');
    expect(attachmentKind('image/png')).toBe('image');
    expect(attachmentKind('image/GIF')).toBe('image');
    expect(attachmentKind('video/mp4')).toBe('video');
    expect(attachmentKind('video/webm')).toBe('video');
    expect(attachmentKind('video/quicktime')).toBe('video');
    expect(attachmentKind('audio/mpeg')).toBe('audio');
    expect(attachmentKind('audio/flac')).toBe('audio');
    expect(attachmentKind('application/pdf')).toBe('file');
    expect(attachmentKind('')).toBe('file');
    expect(attachmentKind(undefined)).toBe('file');
    // SVG stays OUT of inline types (scriptable) — download row only.
    expect(attachmentKind('image/svg+xml')).toBe('file');
  });
});

// Fake scroller for the anchoring math: each message has a content-space top
// and height; its viewport rect is derived from the feed's scrollTop.
function fakeFeed(msgs, { scrollTop = 0, clientHeight = 300 } = {}) {
  const feed = {
    scrollTop, clientHeight, _msgs: msgs,
    get scrollHeight() { return msgs.reduce((h, m) => Math.max(h, m.top + m.height), 0); },
    getBoundingClientRect: () => ({ top: 50, height: clientHeight }),
    querySelectorAll: () => msgs.map(m => ({
      dataset: { messageId: m.id },
      getBoundingClientRect: () => ({ top: 50 + m.top - feed.scrollTop, height: m.height }),
    })),
  };
  return feed;
}
const rows = (ids, start = 0, h = 40) => ids.map((id, i) => ({ id, top: start + i * h, height: h }));

describe('scroll anchoring helpers', () => {
  it('captures the first message intersecting the viewport and its offset', () => {
    const feed = fakeFeed(rows(['a', 'b', 'c', 'd']), { scrollTop: 50 });
    // a spans 0-40 (above), b spans 40-80 and is partly visible at offset -10
    expect(captureScrollAnchor(feed)).toEqual({ id: 'b', offset: -10 });
  });
  it('returns null for an empty or missing feed', () => {
    expect(captureScrollAnchor(fakeFeed([]))).toBeNull();
    expect(captureScrollAnchor(null)).toBeNull();
  });
  it('keeps the anchored message in place after older history is prepended', () => {
    const before = fakeFeed(rows(['m5', 'm6', 'm7', 'm8']), { scrollTop: 0 });
    const anchor = captureScrollAnchor(before);
    expect(anchor).toEqual({ id: 'm5', offset: 0 });
    // Re-render: five older messages now sit above, the DOM was rebuilt and
    // the browser left scrollTop at whatever it was.
    const after = fakeFeed(rows(['m0', 'm1', 'm2', 'm3', 'm4', 'm5', 'm6', 'm7', 'm8']), { scrollTop: 0 });
    expect(restoreScrollAnchor(after, anchor)).toBe(true);
    expect(after.scrollTop).toBe(200);
    expect(captureScrollAnchor(after)).toEqual(anchor);
  });
  it('preserves a partial offset, not just the message', () => {
    const before = fakeFeed(rows(['x', 'y', 'z']), { scrollTop: 15 });
    const anchor = captureScrollAnchor(before); // x at -15
    const after = fakeFeed(rows(['o1', 'o2', 'x', 'y', 'z']), { scrollTop: 0 });
    restoreScrollAnchor(after, anchor);
    expect(after.scrollTop).toBe(95);
  });
  it('reports failure when the anchor is no longer rendered', () => {
    const feed = fakeFeed(rows(['a']), { scrollTop: 0 });
    expect(restoreScrollAnchor(feed, { id: 'gone', offset: 0 })).toBe(false);
    expect(restoreScrollAnchor(feed, null)).toBe(false);
    expect(feed.scrollTop).toBe(0);
  });
  it('isFeedAtBottom uses the 60px slack of the scroll-to-bottom button', () => {
    expect(isFeedAtBottom({ scrollHeight: 1000, scrollTop: 500, clientHeight: 500 })).toBe(true);
    expect(isFeedAtBottom({ scrollHeight: 1000, scrollTop: 441, clientHeight: 500 })).toBe(true);
    expect(isFeedAtBottom({ scrollHeight: 1000, scrollTop: 440, clientHeight: 500 })).toBe(false);
  });
});

describe('media load re-sticks a bottom-pinned feed', () => {
  function attachWithListeners() {
    const listeners = {};
    const feed = mkEl({
      addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    });
    els['message-feed'] = feed;
    els['scroll-bottom-btn'] = mkEl();
    const r = make();
    r.attach();
    const fire = (type, target) => listeners[type].forEach(fn => fn({ type, target }));
    return { r, feed, fire };
  }
  it('scrolls to the new bottom when an image loads while pinned', () => {
    const { feed, fire } = attachWithListeners();
    feed.scrollTop = 500; // at bottom (1000 - 500 - 500 = 0)
    fire('scroll', feed);
    feed.scrollHeight = 1300; // image decoded and grew the feed
    fire('load', { tagName: 'IMG' });
    expect(feed.scrollTop).toBe(1300);
  });
  it('does not yank the reader down after they scrolled up', () => {
    const { feed, fire } = attachWithListeners();
    feed.scrollTop = 200;
    fire('scroll', feed);
    feed.scrollHeight = 1300;
    fire('load', { tagName: 'IMG' });
    expect(feed.scrollTop).toBe(200);
  });
  it('ignores load events from non-media elements', () => {
    const { feed, fire } = attachWithListeners();
    feed.scrollTop = 500;
    fire('scroll', feed);
    feed.scrollHeight = 1300;
    fire('load', { tagName: 'LINK' });
    expect(feed.scrollTop).toBe(500);
  });
});
