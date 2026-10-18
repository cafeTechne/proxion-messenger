// Long rooms: paging older history must cost one page of rows, not a
// re-render of everything already on screen, and the DOM must stay bounded.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createRendering } from './rendering.js';

const ROW_H = 20;

// A small element tree: enough of the DOM for the feed's sibling walking,
// fragments, attribute/dataset handling and row geometry.
function mkNode(nodeType = 1) {
  const n = {
    nodeType, parentNode: null, children: [], id: '', dataset: {}, style: {},
    _attrs: {}, _cls: new Set(), _html: '', textContent: '',
    get className() { return [...n._cls].join(' '); },
    set className(v) { n._cls = new Set(String(v).split(/\s+/).filter(Boolean)); },
    classList: {
      add: (c) => n._cls.add(c), remove: (c) => n._cls.delete(c), contains: (c) => n._cls.has(c),
    },
    get innerHTML() { return n._html; },
    set innerHTML(v) { n._html = v; if (v === '') { n.children.forEach(c => { c.parentNode = null; }); n.children = []; } },
    setAttribute(k, v) {
      n._attrs[k] = String(v);
      if (k.startsWith('data-')) n.dataset[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = String(v);
    },
    getAttribute: (k) => (k in n._attrs ? n._attrs[k] : null),
    removeAttribute: (k) => { delete n._attrs[k]; },
    addEventListener(type, fn) { (n._ls ||= {})[type] = fn; },
    appendChild(c) { return n.insertBefore(c, null); },
    insertBefore(c, ref) {
      const kids = c.nodeType === 11 ? [...c.children] : [c];
      if (c.nodeType === 11) c.children = [];
      for (const k of kids) {
        if (k.parentNode) k.remove();
        k.parentNode = n;
        const i = ref ? n.children.indexOf(ref) : -1;
        if (i < 0) n.children.push(k); else n.children.splice(i, 0, k);
      }
      return c;
    },
    remove() {
      if (!n.parentNode) return;
      const p = n.parentNode;
      p.children.splice(p.children.indexOf(n), 1);
      n.parentNode = null;
    },
    get firstElementChild() { return n.children[0] || null; },
    get lastElementChild() { return n.children[n.children.length - 1] || null; },
    get nextElementSibling() { const s = n.parentNode?.children; return s ? s[s.indexOf(n) + 1] || null : null; },
    get previousElementSibling() { const s = n.parentNode?.children; return s ? s[s.indexOf(n) - 1] || null : null; },
    querySelectorAll(sel) {
      const out = [];
      const walk = (x) => x.children.forEach(c => {
        if (c._cls.has('message') && (sel === '.message' || c.dataset.messageId)) out.push(c);
        walk(c);
      });
      walk(n);
      return out;
    },
    querySelector: () => null,
    closest: () => null,
    // Every direct feed child is ROW_H tall, stacked top to bottom.
    getBoundingClientRect() {
      const p = n.parentNode;
      if (!p) return { top: 0, height: n.clientHeight || 0 };
      return { top: p.children.indexOf(n) * ROW_H - p.scrollTop, height: ROW_H };
    },
  };
  return n;
}

function findById(root, id) {
  for (const c of root.children) {
    if (c.id === id) return c;
    const d = findById(c, id);
    if (d) return d;
  }
  return null;
}

let feed, fixed, host, reactions;
beforeEach(() => {
  feed = mkNode();
  feed.clientHeight = 400;
  feed.scrollTop = 0;
  Object.defineProperty(feed, 'scrollHeight', { get: () => feed.children.length * ROW_H });
  fixed = { 'message-feed': feed, 'scroll-bottom-btn': mkNode(), 'scroll-bottom-count': mkNode() };
  global.WebSocket = { OPEN: 1 };
  global.localStorage = { getItem: () => null, setItem: () => {} };
  global.document = {
    activeElement: null,
    getElementById: (id) => fixed[id] || findById(feed, id),
    createElement: () => mkNode(),
    createDocumentFragment: () => mkNode(11),
    querySelectorAll: () => [],
  };
  // One message a minute, from three senders, all on one day.
  const all = [];
  for (let i = 0; i < 1000; i++) {
    all.push({
      message_id: `m${i}`, thread_id: 'room-1', content: `message ${i}`,
      from_webid: `did:key:z${i % 3}`, timestamp: new Date(Date.UTC(2026, 0, 1, 0, i)).toISOString(),
    });
  }
  host = { allMessages: all, messageMap: Object.fromEntries(all.map(m => [m.message_id, m])) };
  reactions = vi.fn();
});

function make() {
  const r = createRendering({
    getActiveView: () => ({ type: 'room', id: 'room-1', local: true }),
    getSocket: () => ({ readyState: 1, send: () => {} }),
    getSelfWebId: () => 'did:key:zSelf',
    getSelfPubHex: () => null,
    getCurrentDisappearMs: () => 0,
    getMessageMap: () => host.messageMap,
    getAllMessages: () => host.allMessages,
    getUserPresence: () => ({}),
    renderReactions: reactions,
    openCtxMenu: vi.fn(),
    sendUpdateLastRead: vi.fn(),
    getRoomCode: () => 'CODE',
    renderWindow: 100, scrollBatch: 50, maxRendered: 300,
  });
  r.attach();
  r.state._unreadSeen = true;
  r.renderMessages();
  return r;
}

const rowIds = () => feed.querySelectorAll('.message[data-message-id]').map(e => e.dataset.messageId);
const scrollTo = (top) => { feed.scrollTop = top; feed._ls.scroll({ target: feed }); };

describe('windowed message feed', () => {
  it('pages older history one batch at a time and keeps the DOM bounded', () => {
    make();
    expect(rowIds()).toHaveLength(100);
    const perPage = [];
    for (let i = 0; i < 15; i++) {
      reactions.mockClear();
      scrollTo(0);
      perPage.push(reactions.mock.calls.length);
    }
    // Each page draws only its own 50 rows (the old code re-rendered every
    // row already on screen, so this grew with each page).
    expect(perPage.every(n => n === 50)).toBe(true);
    const ids = rowIds();
    expect(ids.length).toBeLessThanOrEqual(300);
    // 15 pages of 50 above the first 100: the oldest rendered is m150.
    expect(ids[0]).toBe('m150');
    // Contiguous and in order.
    ids.forEach((id, i) => expect(id).toBe(`m${150 + i}`));
  });

  it('keeps the row the reader was on in place when a page is prepended', () => {
    make();
    scrollTo(0);
    // The reader reaches the top again: the row at the top of the viewport
    // must still be there after the next page lands above it.
    feed.scrollTop = 0;
    const top = rowIds()[0];
    const before = findById(feed, `msg-${top}`).getBoundingClientRect().top;
    feed._ls.scroll({ target: feed });
    expect(rowIds()[0]).not.toBe(top);
    expect(findById(feed, `msg-${top}`).getBoundingClientRect().top).toBe(before);
  });

  it('renders newer rows again when the reader scrolls back down, and a live message waits behind the button', () => {
    const r = make();
    for (let i = 0; i < 10; i++) scrollTo(0);
    expect(rowIds()).not.toContain('m999');
    // A message arriving while the newest rows are paged out is counted,
    // not drawn after an unrelated older row.
    r.renderMessage({ message_id: 'm1000', thread_id: 'room-1', content: 'new', from_webid: 'did:key:z1',
      timestamp: new Date(Date.UTC(2026, 0, 1, 20)).toISOString() }, { live: true });
    expect(rowIds()).not.toContain('m1000');
    expect(r.state._scrollBottomUnread).toBe(1);
    // Scroll back down to the end of the rendered rows: the next batch comes back.
    const last = rowIds().at(-1);
    scrollTo(feed.scrollHeight - feed.clientHeight);
    const ids = rowIds();
    const n = +last.slice(1);
    expect(ids).toContain(`m${n + 50}`);
    expect(ids.length).toBeLessThanOrEqual(300);
    // Jump to bottom shows the newest messages, including the live one.
    r.scrollToBottom();
    expect(rowIds().at(-1)).toBe('m1000');
  });

  it('revealMessage renders a paged-out message so a reply or pin can jump to it', () => {
    const r = make();
    expect(findById(feed, 'msg-m20')).toBeNull();
    const el = r.revealMessage('m20');
    expect(el && el.dataset.messageId).toBe('m20');
    expect(r.revealMessage('nope')).toBeNull();
  });

  it('a message edited while paged out is drawn with the edit when it comes back', () => {
    const r = make();
    host.messageMap.m10 = { ...host.messageMap.m10, content: 'edited text' };
    r.revealMessage('m10');
    expect(findById(feed, 'msg-m10')).not.toBeNull();
    const body = findById(feed, 'msg-m10').children.find(c => c._cls.has('msg-body'));
    expect(body.innerHTML).toContain('edited text');
  });
});
