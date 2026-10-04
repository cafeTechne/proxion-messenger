import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createModals, forwardTargets, searchAction } from './modals.js';

describe('forwardTargets', () => {
  const ctl = (id, name) => ({
    dataset: { roomId: id },
    closest: () => (name ? { getAttribute: () => name } : null),
  });
  it('labels each room by its sidebar name, not the raw id', () => {
    const doc = { querySelectorAll: () => [ctl('room-a11435c9f740', 'general'), ctl('room-2', 'random')] };
    expect(forwardTargets(doc)).toEqual([
      { id: 'room-a11435c9f740', name: 'general' },
      { id: 'room-2', name: 'random' },
    ]);
  });
  it('falls back to the id when no named row is found, and lists each room once', () => {
    const doc = { querySelectorAll: () => [ctl('room-x', null), ctl('room-x', 'dup')] };
    expect(forwardTargets(doc)).toEqual([{ id: 'room-x', name: 'room-x' }]);
  });
});

let els;
function mkEl(over = {}) {
  const children = [];
  return {
    style: {}, innerHTML: '', textContent: '', className: '', id: '',
    dataset: {}, _children: children,
    appendChild: (c) => children.push(c),
    addEventListener() {}, remove() {}, setAttribute() {},
    querySelector: () => mkEl(), querySelectorAll: () => [],
    ...over,
  };
}
beforeEach(() => {
  els = {};
  global.document = {
    getElementById: (id) => (els[id] ||= mkEl()),
    createElement: () => mkEl(),
    querySelectorAll: () => [],
    body: { appendChild() {} },
  };
});

function make(over = {}) {
  const sent = [];
  const sendCmdCalls = [];
  const socket = { readyState: 1, send: (s) => sent.push(JSON.parse(s)) };
  const modals = createModals({
    getSocket: () => (over.socket === undefined ? socket : over.socket),
    getActiveView: () => (over.activeView === undefined ? { type: 'local_room', id: 'room-1' } : over.activeView),
    sendCmd: (cmd, payload) => sendCmdCalls.push({ cmd, payload }),
    showToast: () => {},
    renderMessage: over.renderMessage ?? (() => {}),
  });
  return { modals, sent, sendCmdCalls };
}

describe('openForwardModal', () => {
  it('records the message id and opens the modal', () => {
    const { modals } = make();
    modals.openForwardModal('m1');
    expect(modals.state.forwardingMsgId).toBe('m1');
    expect(els['forward-modal'].style.display).toBe('flex');
  });
  it('shows an empty-state when there are no rooms', () => {
    const { modals } = make();
    modals.openForwardModal('m1');
    expect(els['forward-thread-list'].innerHTML).toContain('modal.noRoomsToForward');
  });
});

describe('openSchedulePicker', () => {
  it('toggles the picker open then closed', () => {
    const { modals } = make();
    els['schedule-picker'] = mkEl({ style: { display: 'none' } });
    modals.openSchedulePicker();
    expect(els['schedule-picker'].style.display).toBe('flex');
    modals.openSchedulePicker();
    expect(els['schedule-picker'].style.display).toBe('none');
  });
});

describe('openIntegrationsPanel', () => {
  it('requests the webhook list for the active thread', () => {
    const { modals, sent } = make({ activeView: { type: 'local_room', id: 'room-1' } });
    modals.openIntegrationsPanel();
    expect(sent).toContainEqual({ cmd: 'list_webhooks', thread_id: 'room-1' });
  });
  it('is a no-op without an active view', () => {
    const { modals, sent } = make({ activeView: null });
    modals.openIntegrationsPanel();
    expect(sent).toHaveLength(0);
  });
});

describe('searchAction', () => {
  it('closes on an empty box', () => {
    expect(searchAction('   ', true).kind).toBe('close');
  });
  it('asks for more text under 3 characters', () => {
    expect(searchAction('hi', true).kind).toBe('short');
  });
  it('reports offline instead of sending into a closed socket', () => {
    expect(searchAction('hello', false).kind).toBe('offline');
  });
  it('searches with the trimmed query', () => {
    expect(searchAction('  hello ', true)).toEqual({ kind: 'search', query: 'hello' });
  });
});

describe('search panel', () => {
  // A small fake DOM: enough structure for the panel to be inserted before the
  // feed, found again by id, and queried for its title and list.
  function fakeEl(tag) {
    const el = {
      tagName: tag, style: {}, hidden: false, children: [], attrs: {}, listeners: {},
      innerHTML: '', textContent: '', className: '', id: '', value: '', scrollTop: 0, type: '',
      setAttribute(k, v) { el.attrs[k] = v; },
      addEventListener(ev, fn) { el.listeners[ev] = fn; },
      appendChild(c) { el.children.push(c); c.parentNode = el; },
      insertBefore(c) { el.children.unshift(c); c.parentNode = el; },
      focus() { el.focused = true; },
      querySelector(sel) {
        if (!el._parts) {
          el._parts = {
            '#search-panel-title': fakeEl('h2'),
            '[data-search-close]': fakeEl('button'),
            '[data-search-results]': fakeEl('ul'),
          };
        }
        return el._parts[sel] || null;
      },
    };
    return el;
  }
  let dom, sent, opened;
  beforeEach(() => {
    vi.useFakeTimers();
    const main = fakeEl('main');
    dom = { 'message-feed': fakeEl('div'), 'search-input': fakeEl('input'), 'search-hint': fakeEl('p') };
    main.appendChild(dom['message-feed']);
    global.document = {
      getElementById: (id) => dom[id] || null,
      createElement: (tag) => {
        const el = fakeEl(tag);
        // Register the panel by id once main assigns it.
        return new Proxy(el, { set(t, k, v) { t[k] = v; if (k === 'id' && v) dom[v] = el; return true; } });
      },
      body: { appendChild() {} },
    };
    sent = []; opened = [];
  });
  afterEach(() => vi.useRealTimers());

  function mk(online = true) {
    const socket = { readyState: online ? 1 : 3, send: (s) => sent.push(JSON.parse(s)) };
    return createModals({
      getSocket: () => socket, getActiveView: () => null, sendCmd() {}, showToast() {},
      openSearchResult: (thread, msg) => opened.push([thread, msg]),
    });
  }

  it('hides the feed without clearing it and titles the panel with the query', () => {
    const m = mk();
    dom['message-feed'].innerHTML = '<div>conversation</div>';
    dom['message-feed'].scrollTop = 420;
    dom['search-input'].value = 'hello';
    m.renderSearchResults({ query: 'hello', results: [{ message_id: 'm1', thread_id: 'r1', content: 'hello there' }] });
    expect(dom['search-panel'].hidden).toBe(false);
    expect(dom['message-feed'].style.display).toBe('none');
    expect(dom['message-feed'].innerHTML).toBe('<div>conversation</div>');
    expect(dom['search-panel'].querySelector('#search-panel-title').textContent).toBe('search.resultsFor');
    expect(dom['search-panel'].querySelector('[data-search-results]').children).toHaveLength(1);
  });

  it('shows the no-match notice inside the panel', () => {
    const m = mk();
    dom['search-input'].value = 'zzz';
    m.renderSearchResults({ query: 'zzz', results: [] });
    expect(dom['search-panel'].querySelector('[data-search-results]').innerHTML).toContain('search.noMatches');
  });

  it('closing restores the feed and its scroll position, and clears the box', () => {
    const m = mk();
    dom['message-feed'].scrollTop = 420;
    dom['search-input'].value = 'hello';
    m.renderSearchResults({ query: 'hello', results: [] });
    dom['message-feed'].scrollTop = 0;
    expect(m.closeSearch({ focusInput: true })).toBe(true);
    expect(dom['search-panel'].hidden).toBe(true);
    expect(dom['message-feed'].style.display).toBe('');
    expect(dom['message-feed'].scrollTop).toBe(420);
    expect(dom['search-input'].value).toBe('');
    expect(dom['search-input'].focused).toBe(true);
    expect(m.closeSearch()).toBe(false);
  });

  it('the close button closes the panel', () => {
    const m = mk();
    dom['search-input'].value = 'hello';
    m.renderSearchResults({ query: 'hello', results: [] });
    dom['search-panel'].querySelector('[data-search-close]').listeners.click();
    expect(m.isSearchOpen()).toBe(false);
  });

  it('clearing the input closes the panel', () => {
    const m = mk();
    dom['search-input'].value = 'hello';
    m.renderSearchResults({ query: 'hello', results: [] });
    m.onSearchInput('');
    expect(m.isSearchOpen()).toBe(false);
    expect(dom['message-feed'].style.display).toBe('');
  });

  it('ignores a late reply for a query the box no longer holds', () => {
    const m = mk();
    dom['search-input'].value = '';
    m.renderSearchResults({ query: 'hello', results: [] });
    expect(dom['search-panel']).toBeUndefined();
  });

  it('explains short queries and offline search instead of doing nothing', () => {
    mk().onSearchInput('hi');
    expect(dom['search-hint'].textContent).toBe('search.tooShort');
    mk(false).onSearchInput('hello');
    expect(dom['search-hint'].textContent).toBe('search.offline');
    vi.runAllTimers();
    expect(sent).toHaveLength(0);
  });

  it('sends the search after the debounce when online', () => {
    const m = mk();
    m.onSearchInput(' hello ');
    expect(sent).toHaveLength(0);
    vi.advanceTimersByTime(500);
    expect(sent).toEqual([{ cmd: 'search', query: 'hello' }]);
    expect(dom['search-hint'].textContent).toBe('');
  });

  it('clicking a result closes the panel and opens the message', () => {
    const m = mk();
    dom['search-input'].value = 'hello';
    m.renderSearchResults({ query: 'hello', results: [{ message_id: 'm1', thread_id: 'r1', content: 'hello' }] });
    const li = dom['search-panel'].querySelector('[data-search-results]').children[0];
    li.children[0].listeners.click();
    expect(m.isSearchOpen()).toBe(false);
    expect(opened).toEqual([['r1', 'm1']]);
  });
});
