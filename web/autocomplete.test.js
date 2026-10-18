// autocomplete.js + the two composer popups (mentions.js, emoji.js): the popup
// is placed next to the composer (it used to open at 0,0), is a real
// listbox the input points into with aria-activedescendant, and is styled.
import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { positionPopup, closeListbox, openListbox, setActiveOption } from './autocomplete.js';
import { createMentions, matchMembers } from './mentions.js';
import { createEmoji, matchShortcodes } from './emoji.js';

// Minimal DOM double (vitest runs in the node environment).
class El {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.id = ''; this.style = {}; this.dataset = {};
    this.attrs = {}; this.children = []; this.listeners = {}; this.textContent = '';
    this.value = ''; this.selectionStart = 0; this.offsetWidth = 200; this.offsetHeight = 120;
    const cls = new Set();
    this.classList = {
      toggle: (c, on) => { (on ?? !cls.has(c)) ? cls.add(c) : cls.delete(c); },
      contains: (c) => cls.has(c), add: (c) => cls.add(c),
    };
    Object.defineProperty(this, 'className', {
      get: () => [...cls].join(' '),
      set: (v) => { cls.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => cls.add(c)); },
    });
  }
  set innerHTML(v) { if (v === '') this.children = []; }
  get innerHTML() { return ''; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); }
  fire(t, ev = {}) { (this.listeners[t] || []).forEach((fn) => fn({ preventDefault() {}, ...ev })); }
  querySelectorAll(sel) {
    if (sel === '.mention-option') return this.children.filter((c) => c.classList.contains('mention-option'));
    return [];
  }
  setSelectionRange(a) { this.selectionStart = a; }
  focus() {}
  closest() { return null; }
  getBoundingClientRect() { return this.rect || { left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0 }; }
}

let els;
beforeEach(() => {
  els = {};
  globalThis.document = {
    getElementById: (id) => els[id] || null,
    createElement: (t) => new El(t),
    addEventListener() {},
    documentElement: { dir: '' },
  };
  globalThis.window = { innerWidth: 1280, innerHeight: 800, document: globalThis.document };
});

function mkInput() {
  const input = new El('textarea');
  input.id = 'message-input';
  input.rect = { left: 480, right: 1070, top: 752, bottom: 788, width: 590, height: 36 };
  return input;
}
function mkDd(id) { const dd = new El('ul'); dd.id = id; dd.style.display = 'none'; els[id] = dd; return dd; }
function type(input, text) { input.value = text; input.selectionStart = text.length; input.fire('input'); }
function key(input, k) { input.fire('keydown', { key: k }); }

describe('positionPopup', () => {
  it('opens above the composer input, aligned to its start edge', () => {
    const dd = mkDd('mention-dropdown');
    positionPopup(dd, mkInput());
    expect(dd.style.left).toBe('480px');
    expect(dd.style.bottom).toBe(`${800 - 752 + 4}px`);
    expect(dd.style.top).toBe('auto');
  });
  it('keeps the popup inside the viewport', () => {
    const dd = mkDd('mention-dropdown');
    const input = mkInput();
    input.rect = { ...input.rect, left: 1200 };
    positionPopup(dd, input);
    expect(parseInt(dd.style.left, 10)).toBeLessThanOrEqual(1280 - 200 - 8);
  });
  it('flips below when the input is near the top', () => {
    const dd = mkDd('mention-dropdown');
    const input = mkInput();
    input.rect = { left: 10, right: 400, top: 20, bottom: 56, width: 390, height: 36 };
    positionPopup(dd, input);
    expect(dd.style.top).toBe('60px');
    expect(dd.style.bottom).toBe('auto');
  });
});

describe('listbox wiring', () => {
  it('closing the idle popup leaves the open one hooked up', () => {
    const input = mkInput();
    const a = mkDd('mention-dropdown');
    const b = mkDd('emoji-dropdown');
    openListbox(input, a);
    setActiveOption(input, [], 0);
    closeListbox(input, b);
    expect(input.getAttribute('aria-controls')).toBe('mention-dropdown');
    closeListbox(input, a);
    expect(input.getAttribute('aria-controls')).toBe(null);
  });
});

describe('matchMembers', () => {
  const members = [
    { display_name: 'Alice', webid: 'did:key:z6MkbBbB' },
    { display_name: 'Bob', webid: 'did:key:z6Mkzzzz' },
    { display_name: 'Anna Bell', webid: 'did:key:z6Mkyyyy' },
    { display_name: 'Robert', webid: 'did:key:z6Mkxxxx' },
  ];
  it('does not match a name on a letter buried in the middle or in the DID', () => {
    const names = matchMembers(members, 'b').map((m) => m.display_name);
    expect(names).not.toContain('Alice');
    expect(names).not.toContain('Robert');
  });
  it('puts name-prefix matches before word-prefix matches', () => {
    expect(matchMembers(members, 'b').map((m) => m.display_name)).toEqual(['Bob', 'Anna Bell']);
  });
  it('lists everyone for a bare @', () => {
    expect(matchMembers(members, '')).toHaveLength(4);
  });
});

describe('matchShortcodes (tightened)', () => {
  it('matches word starts, not arbitrary infixes', () => {
    const names = matchShortcodes('art', 50).map((m) => m.name);
    expect(names).not.toContain('heart');
    const smile = matchShortcodes('smile', 50).map((m) => m.name);
    expect(smile[0]).toBe('smile');
    expect(smile).toContain('sweat_smile');
  });
});

describe('mention popup', () => {
  function setup() {
    const input = mkInput();
    const dd = mkDd('mention-dropdown');
    const m = createMentions({ getCurrentRoomMembers: () => [
      { display_name: 'Bob', webid: 'did:key:z6Mkb1' },
      { display_name: 'Bobby', webid: 'did:key:z6Mkb2' },
    ] });
    m.attach(input);
    return { input, dd, m };
  }
  it('renders listbox options and places the popup by the composer', () => {
    const { input, dd } = setup();
    type(input, 'hi @b');
    expect(dd.style.display).toBe('block');
    expect(dd.style.left).toBe('480px');
    expect(dd.children).toHaveLength(2);
    expect(dd.children[0].tagName).toBe('LI');
    expect(dd.children[0].getAttribute('role')).toBe('option');
    expect(dd.children[0].getAttribute('aria-selected')).toBe('true');
    expect(input.getAttribute('aria-controls')).toBe('mention-dropdown');
    expect(input.getAttribute('aria-activedescendant')).toBe(dd.children[0].id);
  });
  it('arrow keys move aria-activedescendant, Enter inserts, Escape closes', () => {
    const { input, dd } = setup();
    type(input, '@bo');
    key(input, 'ArrowDown');
    expect(input.getAttribute('aria-activedescendant')).toBe(dd.children[1].id);
    expect(dd.children[1].getAttribute('aria-selected')).toBe('true');
    expect(dd.children[0].getAttribute('aria-selected')).toBe('false');
    key(input, 'Enter');
    expect(input.value).toBe('@Bobby ');
    expect(dd.style.display).toBe('none');
    expect(input.getAttribute('aria-activedescendant')).toBe(null);
    type(input, '@bo');
    key(input, 'Escape');
    expect(dd.style.display).toBe('none');
  });
});

describe('emoji popup', () => {
  it('renders listbox options, positioned, with aria-activedescendant', () => {
    const input = mkInput();
    const dd = mkDd('emoji-dropdown');
    const e = createEmoji({});
    e.attach(input);
    type(input, 'nice :fir');
    expect(dd.style.display).toBe('block');
    expect(dd.style.left).toBe('480px');
    expect(dd.children[0].getAttribute('role')).toBe('option');
    expect(input.getAttribute('aria-activedescendant')).toBe(dd.children[0].id);
    key(input, 'Escape');
    expect(dd.style.display).toBe('none');
  });
});

describe('popup styling exists', () => {
  const css = readFileSync(new URL('./style.css', import.meta.url), 'utf8');
  const html = readFileSync(new URL('./index.html', import.meta.url), 'utf8');
  it('style.css styles the popup, its options and the active option', () => {
    expect(css).toMatch(/\.ac-popup\s*\{/);
    expect(css).toMatch(/\.ac-option\s*\{/);
    expect(css).toMatch(/\.ac-option\.focused|\.ac-option\[aria-selected="true"\]/);
  });
  it('both dropdowns are listboxes using the shared class', () => {
    for (const id of ['mention-dropdown', 'emoji-dropdown']) {
      const tag = html.match(new RegExp(`<ul id="${id}"[^>]*>`))?.[0] || '';
      expect(tag).toContain('role="listbox"');
      expect(tag).toContain('class="ac-popup"');
    }
  });
});
