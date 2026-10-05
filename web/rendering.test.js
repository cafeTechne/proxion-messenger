import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  createRendering, captureScrollAnchor, restoreScrollAnchor, isFeedAtBottom,
  replySnippet, replyQuoteHtml, highlightMentions, dateLabel, timeHtml, GROUP_WINDOW_MS,
} from './rendering.js';

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

describe('hover action bar', () => {
  function renderHtml(msg) {
    els['message-feed'] = mkEl({ scrollHeight: 500, clientHeight: 500 });
    const created = [];
    const orig = global.document.createElement;
    global.document.createElement = () => { const el = mkEl(); created.push(el); return el; };
    try {
      make().renderMessage({ thread_id: 'room-1', content: 'hi', timestamp: new Date().toISOString(), ...msg });
      return created.map(e => e.innerHTML).join('');
    } finally {
      global.document.createElement = orig;
    }
  }
  const barOf = (html) => html.slice(html.indexOf('<div class="msg-actions">'));
  const actions = (html) => [...barOf(html).matchAll(/data-msg-action="([\w-]+)"/g)].map(m => m[1]);

  it('shows react, reply, edit and more for own messages, nothing else', () => {
    const html = renderHtml({ message_id: 'm1', from_webid: 'did:key:zSelf', local: true,
      file: { data_b64: 'AAAA', mime_type: 'image/png', filename: 'a.png', size: 3 } });
    expect(actions(html)).toEqual(['react', 'reply', 'edit', 'more']);
  });

  it("drops edit on other people's messages", () => {
    const html = renderHtml({ message_id: 'm2', from_webid: 'did:key:zOther' });
    expect(actions(html)).toEqual(['react', 'reply', 'more']);
  });

  it('uses SVG icons with a translated name instead of text glyphs', () => {
    const bar = barOf(renderHtml({ message_id: 'm3', from_webid: 'did:key:zSelf' }));
    for (const key of ['msg.react', 'msg.reply', 'msg.edit', 'ui.moreActions']) {
      expect(bar).toContain(`aria-label="${key}" title="${key}"`);
    }
    expect(bar).not.toMatch(/style="min-width/);
    expect(bar).not.toMatch(/>\+<|>M<|&#8599;|&#128278;|&#9734;/);
    expect((bar.match(/<svg /g) || []).length).toBe(4);
  });

  it('renders the delivery tick as an icon with a hidden status word', () => {
    const html = renderHtml({ message_id: 'm4', from_webid: 'did:key:zSelf' });
    expect(html).toContain('class="read-receipt"');
    expect(html).toContain('<span class="sr-only">receipt.sent</span>');
    expect(html).not.toContain('&#10003;');
  });
});

// ---------------------------------------------------------------------------
// A tiny DOM (tree, classList, dataset, simple selectors) for the feed-order,
// grouping, divider and delete tests. innerHTML is stored, not parsed.
class MiniEl {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase();
    this.children = []; this.parentNode = null;
    this.dataset = {}; this.style = {}; this._attrs = {}; this._html = '';
    this.id = ''; this.className = ''; this.scrollTop = 0; this.textContent = '';
  }
  get classList() {
    const el = this;
    const list = () => el.className.split(/\s+/).filter(Boolean);
    return {
      add(...c) { el.className = [...new Set([...list(), ...c])].join(' '); },
      remove(...c) { el.className = list().filter(x => !c.includes(x)).join(' '); },
      contains(c) { return list().includes(c); },
    };
  }
  _dkey(k) { return k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase()); }
  setAttribute(k, v) {
    v = String(v);
    if (k === 'id') this.id = v;
    else if (k === 'class') this.className = v;
    else if (k.startsWith('data-')) this.dataset[this._dkey(k)] = v;
    else this._attrs[k] = v;
  }
  getAttribute(k) {
    if (k.startsWith('data-')) return this.dataset[this._dkey(k)] ?? null;
    return this._attrs[k] ?? null;
  }
  addEventListener() {}
  appendChild(c) { c.remove(); c.parentNode = this; this.children.push(c); return c; }
  insertBefore(c, ref) {
    c.remove(); c.parentNode = this;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i < 0) this.children.push(c); else this.children.splice(i, 0, c);
    return c;
  }
  remove() {
    if (!this.parentNode) return;
    const p = this.parentNode;
    p.children.splice(p.children.indexOf(this), 1);
    this.parentNode = null;
  }
  replaceWith(n) { const p = this.parentNode; if (!p) return; p.insertBefore(n, this); this.remove(); }
  get nextElementSibling() { const p = this.parentNode; return p ? p.children[p.children.indexOf(this) + 1] || null : null; }
  get firstElementChild() { return this.children[0] || null; }
  get lastElementChild() { return this.children[this.children.length - 1] || null; }
  get innerHTML() { return this._html; }
  set innerHTML(v) {
    this._html = v;
    if (v === '') { this.children.forEach(c => { c.parentNode = null; }); this.children = []; }
  }
  getBoundingClientRect() { return { top: 0, height: 0, left: 0 }; }
  _all() { return this.children.flatMap(c => [c, ...c._all()]); }
  matches(sel) {
    const parts = sel.match(/\.[\w-]+|\[[^\]]+\]|#[\w-]+|^[a-z]+/gi) || [];
    return parts.every(p => {
      if (p[0] === '.') return this.classList.contains(p.slice(1));
      if (p[0] === '#') return this.id === p.slice(1);
      if (p[0] === '[') {
        const m = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(p);
        const v = this.getAttribute(m[1]);
        return m[2] === undefined ? v != null : v === m[2];
      }
      return this.tagName === p.toUpperCase();
    });
  }
  querySelectorAll(sel) {
    const last = sel.trim().split(/\s+/).pop(); // descendant prefixes ignored
    return this._all().filter(e => e.matches(last));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

function miniDom() {
  const root = new MiniEl('body');
  const feed = root.appendChild(new MiniEl());
  feed.id = 'message-feed';
  feed.scrollHeight = 500; feed.clientHeight = 500; // always "at bottom"
  const btn = root.appendChild(new MiniEl()); btn.id = 'scroll-bottom-btn';
  const cnt = root.appendChild(new MiniEl()); cnt.id = 'scroll-bottom-count';
  global.document = {
    getElementById: (id) => root._all().find(e => e.id === id) || null,
    createElement: (tag) => new MiniEl(tag),
    querySelectorAll: (sel) => root.querySelectorAll(sel),
    querySelector: (sel) => root.querySelector(sel),
  };
  global.CSS = { escape: (s) => s };
  return { root, feed, btn, cnt };
}
const msgIds = (feed) => feed.children.filter(c => c.classList.contains('message')).map(c => c.dataset.messageId);
const bodyOf = (feed, id) => feed.children.find(c => c.dataset.messageId === id).children.find(c => c.className === 'msg-body');
const at = (iso) => new Date(iso).toISOString();

describe('feed order: replies stay in timestamp order', () => {
  it('appends a live reply to an old message at the bottom with an inline quote', () => {
    const { feed } = miniDom();
    const r = make();
    r.renderMessage({ message_id: 'old', thread_id: 'room-1', from_webid: 'did:key:zA', content: 'first', timestamp: at('2026-10-04T10:00:00') });
    r.renderMessage({ message_id: 'mid', thread_id: 'room-1', from_webid: 'did:key:zB', content: 'second', timestamp: at('2026-10-04T10:30:00') });
    r.renderMessage({ message_id: 'rep', thread_id: 'room-1', from_webid: 'did:key:zB', content: 'answer', reply_to_id: 'old', timestamp: at('2026-10-04T11:00:00') });
    expect(msgIds(feed)).toEqual(['old', 'mid', 'rep']);
    const rep = feed.children.find(c => c.dataset.messageId === 'rep');
    expect(rep.classList.contains('reply-nested')).toBe(false);
    const html = bodyOf(feed, 'rep').innerHTML;
    expect(html).toContain('class="reply-context" data-msg-action="scroll-reply" data-reply-id="old"');
    expect(html).toContain('first');
  });
  it('_renderThreaded keeps the given order and never nests', () => {
    const { feed } = miniDom();
    const r = make();
    r._renderThreaded([
      { message_id: 'a', from_webid: 'x', timestamp: at('2026-10-04T10:00:00') },
      { message_id: 'c', from_webid: 'y', timestamp: at('2026-10-04T10:01:00') },
      { message_id: 'b', from_webid: 'y', reply_to_id: 'a', timestamp: at('2026-10-04T10:02:00') },
    ], feed);
    expect(msgIds(feed)).toEqual(['a', 'c', 'b']);
  });
});

describe('entry motion hooks', () => {
  const m = (id, min) => ({ message_id: id, thread_id: 'room-1', from_webid: 'did:key:zA', content: id, timestamp: at(`2026-10-04T10:0${min}:00`) });
  const entered = (feed, id) => feed.children.find(c => c.dataset.messageId === id).classList.contains('msg-enter');
  it('marks only live arrivals with .msg-enter, not history or re-renders', () => {
    const { feed } = miniDom();
    const r = make();
    r.renderMessage(m('hist', 0));
    r.renderMessage(m('live', 1), { live: true });
    expect(entered(feed, 'hist')).toBe(false);
    expect(entered(feed, 'live')).toBe(true);
    // The server echo of an already-rendered message does not re-tag it.
    r.renderMessage(m('hist', 0), { live: true });
    expect(entered(feed, 'hist')).toBe(false);
    host.allMessages = [m('hist', 0), m('live', 1)];
    r.renderMessages();
    expect(entered(feed, 'live')).toBe(false);
  });
  it('fades the unread divider in once, not on every re-render', () => {
    const { feed } = miniDom();
    const r = make();
    host.allMessages = [m('r1', 0), m('u1', 2)];
    r.markUnread(host.allMessages, new Date('2026-10-04T10:01:00').getTime() / 1000);
    r.renderMessages();
    expect(feed.querySelector('.unread-divider').classList.contains('unread-divider--enter')).toBe(true);
    r.renderMessages();
    expect(feed.querySelectorAll('.unread-divider')).toHaveLength(1);
    expect(feed.querySelector('.unread-divider').classList.contains('unread-divider--enter')).toBe(false);
  });
});

describe('reply quote text', () => {
  it('uses the parent text, collapsed to one line', () => {
    expect(replySnippet({ content: 'hello\n  world' })).toBe('hello world');
  });
  it('falls back to Photo / file name / voice for messages without text', () => {
    expect(replySnippet({ content: '', file: { mime_type: 'image/png', filename: 'a.png' } })).toBe('msg.replyPhoto');
    expect(replySnippet({ file: { mime_type: 'application/pdf', filename: 'report.pdf' } })).toBe('report.pdf');
    expect(replySnippet({ file: { mime_type: 'application/pdf', filename: '' } })).toBe('msg.replyAttachment');
    expect(replySnippet({ content_type: 'audio' })).toBe('msg.replyVoice');
  });
  it('says the original was deleted for a tombstone', () => {
    expect(replySnippet({ deleted: true })).toBe('msg.replyDeleted');
    expect(replyQuoteHtml({ deleted: true })).toContain('reply-deleted');
  });
  it('escapes the author and snippet', () => {
    const html = replyQuoteHtml({ from_display_name: '<b>x', content: '<img src=x onerror=1>' });
    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;img');
    expect(html).toContain('&lt;b&gt;x');
  });
});

describe('timestamps', () => {
  it('renders a <time> with an ISO datetime and the full date as title', () => {
    const html = timeHtml('2026-10-04T15:07:00Z', 'msg-ts-time');
    expect(html).toMatch(/^<time class="msg-ts-time" datetime="2026-10-04T15:07:00.000Z" title="[^"]+">[^<]+<\/time>$/);
    expect(html).toContain('2026');
  });
  it('header shows clock time, not a relative "ago" string', () => {
    const { feed } = miniDom();
    const r = make();
    r.renderMessage({ message_id: 't1', thread_id: 'room-1', from_webid: 'did:key:zA', content: 'x', timestamp: new Date(Date.now() - 5 * 60000).toISOString() });
    const html = bodyOf(feed, 't1').innerHTML;
    expect(html).toContain('<time class="msg-ts-time"');
    expect(html).not.toMatch(/ago|time\.justNow/);
  });
  it('date labels carry the weekday, and the year only when it differs', () => {
    const now = new Date('2026-10-04T12:00:00');
    const sameYear = dateLabel('2026-03-05T12:00:00', now);
    expect(sameYear).toContain('Thursday');
    expect(sameYear).not.toContain('2026');
    expect(dateLabel('2020-03-05T12:00:00', now)).toContain('2020');
    expect(dateLabel(now.toISOString(), now)).toBe('time.today');
  });
});

describe('grouping', () => {
  function render(list) {
    const { feed } = miniDom();
    const r = make();
    list.forEach(m => r.renderMessage({ thread_id: 'room-1', content: 'x', ...m }));
    return feed;
  }
  const grouped = (feed, id) => feed.children.find(c => c.dataset.messageId === id).classList.contains('msg-grouped');
  it('groups the same sender within five minutes', () => {
    expect(GROUP_WINDOW_MS).toBe(5 * 60 * 1000);
    const feed = render([
      { message_id: 'a', from_webid: 'w', timestamp: at('2026-10-04T10:00:00') },
      { message_id: 'b', from_webid: 'w', timestamp: at('2026-10-04T10:04:00') },
      { message_id: 'c', from_webid: 'w', timestamp: at('2026-10-04T10:10:00') },
    ]);
    expect(grouped(feed, 'b')).toBe(true);
    expect(grouped(feed, 'c')).toBe(false);
  });
  it('breaks the group at a date divider', () => {
    const feed = render([
      { message_id: 'a', from_webid: 'w', timestamp: at('2026-10-03T23:58:00') },
      { message_id: 'b', from_webid: 'w', timestamp: at('2026-10-04T00:01:00') },
    ]);
    expect(feed.children.filter(c => c.classList.contains('date-divider'))).toHaveLength(2);
    expect(grouped(feed, 'b')).toBe(false);
  });
  it('a reply always starts its own group', () => {
    const feed = render([
      { message_id: 'a', from_webid: 'w', timestamp: at('2026-10-04T10:00:00') },
      { message_id: 'b', from_webid: 'w', reply_to_id: 'a', timestamp: at('2026-10-04T10:01:00') },
    ]);
    expect(grouped(feed, 'b')).toBe(false);
  });
});

describe('"New messages" divider', () => {
  const msgs = [
    { message_id: 'r1', thread_id: 'room-1', from_webid: 'w', content: 'read', timestamp: at('2026-10-04T10:00:00') },
    { message_id: 'u1', thread_id: 'room-1', from_webid: 'w', content: 'new', timestamp: at('2026-10-04T10:02:00') },
    { message_id: 'u2', thread_id: 'room-1', from_webid: 'w', content: 'new 2', timestamp: at('2026-10-04T10:03:00') },
  ];
  const lastRead = new Date('2026-10-04T10:01:00').getTime() / 1000;
  it('goes before the first message newer than the read marker and breaks grouping', () => {
    const { feed } = miniDom();
    const r = make();
    expect(r.markUnread(msgs, lastRead)).toBe('u1');
    msgs.forEach(m => r.renderMessage({ ...m }));
    const kinds = feed.children.map(c => c.className.split(' ')[0] + (c.dataset.messageId ? ':' + c.dataset.messageId : ''));
    expect(kinds).toEqual(['date-divider', 'message:r1', 'unread-divider', 'message:u1', 'message:u2']);
    expect(feed.children[2].innerHTML).toContain('feed.unreadDivider');
    expect(feed.children[3].classList.contains('msg-grouped')).toBe(false);
    expect(feed.children[4].classList.contains('msg-grouped')).toBe(true);
  });
  it('is skipped when there is no read marker or nothing newer', () => {
    const r = make();
    expect(r.markUnread(msgs, 0)).toBe(null);
    expect(r.markUnread(msgs, new Date('2026-10-05').getTime() / 1000)).toBe(null);
  });
  it('survives a re-render, and is cleared on send only after the bottom was reached', () => {
    const { feed } = miniDom();
    const r = make();
    r.markUnread(msgs, lastRead);
    host.allMessages = msgs.map(m => ({ ...m }));
    r.renderMessages();
    expect(feed.querySelectorAll('.unread-divider')).toHaveLength(1);
    r.state._unreadSeen = false;
    expect(r.clearUnreadDivider()).toBe(false);
    expect(feed.querySelectorAll('.unread-divider')).toHaveLength(1);
    r.scrollToBottom();
    expect(r.clearUnreadDivider()).toBe(true);
    expect(feed.querySelectorAll('.unread-divider')).toHaveLength(0);
  });
  it('resetUnread (switching away) drops the marker', () => {
    const r = make();
    r.markUnread(msgs, lastRead);
    r.resetUnread();
    expect(r.state._unreadBeforeId).toBe(null);
  });
  it('the scroll-to-bottom button counts arrivals as "N new messages"', () => {
    const { feed, cnt } = miniDom();
    feed.scrollHeight = 2000; // scrolled up
    const r = make();
    r.renderMessage({ ...msgs[0] });
    r.renderMessage({ ...msgs[1] });
    expect(r.state._scrollBottomUnread).toBe(2);
    expect(cnt.textContent).toBe('feed.newMessages'); // tn() key: no locale loaded in tests
  });
});

describe('deleting a message', () => {
  function setup() {
    const dom = miniDom();
    const r = make();
    [
      { message_id: 'h', from_webid: 'w', content: 'head', timestamp: at('2026-10-04T10:00:00') },
      { message_id: 'g', from_webid: 'w', content: 'grouped', timestamp: at('2026-10-04T10:01:00') },
    ].forEach(m => r.renderMessage({ thread_id: 'room-1', ...m }));
    return { ...dom, r };
  }
  it('re-heads the group when its head is deleted', () => {
    const { feed, r } = setup();
    const g = feed.children.find(c => c.dataset.messageId === 'g');
    expect(g.classList.contains('msg-grouped')).toBe(true);
    expect(g.children[0].innerHTML).toContain('msg-compact-ts');
    r.removeMessage('h');
    expect(msgIds(feed)).toEqual(['g']);
    expect(g.classList.contains('msg-grouped')).toBe(false);
    expect(g.id).toBe('msg-g');
    // The compact time column was swapped for the avatar.
    expect(g.children[0].innerHTML).toContain('data-profile-avatar');
  });
  it('keeps a tombstone so replies say it was deleted without re-fetching', () => {
    const { feed, r } = setup();
    r.removeMessage('h');
    expect(host.messageMap.h).toMatchObject({ message_id: 'h', deleted: true });
    sent.length = 0;
    r.renderMessage({ message_id: 'rep', thread_id: 'room-1', from_webid: 'v', content: 'reply', reply_to_id: 'h', timestamp: at('2026-10-04T10:05:00') });
    expect(bodyOf(feed, 'rep').innerHTML).toContain('msg.replyDeleted');
    expect(sent.filter(m => m.cmd === 'get_message')).toHaveLength(0);
  });
  it('asks for a missing reply parent only once across re-renders', () => {
    miniDom();
    const r = make();
    host.allMessages = [{ message_id: 'x', thread_id: 'room-1', from_webid: 'v', content: 'r', reply_to_id: 'gone', timestamp: at('2026-10-04T10:05:00') }];
    r.renderMessages();
    r.renderMessages();
    expect(sent.filter(m => m.cmd === 'get_message')).toEqual([{ cmd: 'get_message', message_id: 'gone' }]);
  });
});

describe('mentions', () => {
  it('highlights a known multi-word or non-ASCII name in full', () => {
    const out = highlightMentions('hi @Ana María and @Zoë!', ['Ana María', 'Zoë']);
    expect(out).toContain('<span class="mention">@Ana María</span>');
    expect(out).toContain('<span class="mention">@Zoë</span>!');
  });
  it('prefers the longest known name and marks self mentions', () => {
    const out = highlightMentions('@Bob Smith and @Bob', ['Bob', 'Bob Smith'], 'bob');
    expect(out).toContain('<span class="mention">@Bob Smith</span>');
    expect(out).toContain('<span class="mention mention-self">@Bob</span>');
  });
  it('falls back to a single word for unknown names, including non-ASCII', () => {
    expect(highlightMentions('@Łukasz hi', [])).toBe('<span class="mention">@Łukasz</span> hi');
  });
  it('matches names that need HTML escaping against the escaped text', () => {
    expect(highlightMentions('@Tom &amp; Jerry', ['Tom & Jerry'])).toContain('@Tom &amp; Jerry</span>');
  });
  it('uses msg.mentions resolved through resolveName', () => {
    const { feed } = miniDom();
    const r = make({ resolveName: (w) => (w === 'did:key:zAna' ? 'Ana María' : '') });
    r.renderMessage({ message_id: 'mm', thread_id: 'room-1', from_webid: 'did:key:zB', content: 'ping @Ana María', mentions: ['did:key:zAna'], timestamp: at('2026-10-04T10:00:00') });
    expect(bodyOf(feed, 'mm').innerHTML).toContain('<span class="mention">@Ana María</span>');
  });
});

describe('scroll to top with the whole buffer rendered', () => {
  function scrollTop(renderedCount) {
    const listeners = {};
    const all = Array.from({ length: 150 }, (_, i) => ({ message_id: 'm' + i, timestamp: at('2026-10-04T10:00:00') }));
    host.allMessages = all;
    const feed = mkEl({
      addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
      querySelectorAll: (sel) => (sel === '.message' ? all.slice(150 - renderedCount).map(() => ({})) : []),
    });
    els['message-feed'] = feed;
    els['scroll-bottom-btn'] = mkEl();
    view = { type: 'local_room', id: 'room-1', local: true };
    const r = make();
    r.attach();
    feed.scrollTop = 0; feed.scrollHeight = 5000;
    listeners.scroll.forEach(fn => fn({ target: feed }));
    return all;
  }
  it('requests older history instead of re-expanding the in-memory buffer', () => {
    const all = scrollTop(150);
    expect(sent).toContainEqual({ cmd: 'get_local_history', thread_id: 'room-1', before_timestamp: all[0].timestamp, limit: 50 });
  });
  it('still expands the buffer first while some of it is not rendered', () => {
    scrollTop(100);
    expect(sent.filter(m => m.cmd === 'get_local_history')).toHaveLength(0);
  });
});
