import { describe, it, expect, beforeEach, vi } from 'vitest';
import { makeListNavigable, announce } from './a11y.js';

// The repo runs vitest under the `node` environment (no jsdom), so these tests
// build minimal DOM doubles — the same idiom the other *.test.js files use.

// A fake <li> row with the surface makeListNavigable touches.
function mkItem({ active = false, buttons = 0 } = {}) {
  const kids = Array.from({ length: buttons }, () => ({ tagName: 'BUTTON', tabIndex: 0 }));
  const el = {
    tagName: 'LI',
    tabIndex: -1,
    _focused: 0,
    classList: { contains: (c) => (c === 'active' ? active : false) },
    contains(t) { return t === el; },
    focus() { el._focused++; },
    click: vi.fn(),
    querySelectorAll: () => kids,
    _kids: kids,
  };
  return el;
}

// A fake list element; captures the keydown handler so tests can drive it.
function mkList(items) {
  let keydown = null;
  const el = {
    tagName: 'UL',
    _a11yNav: undefined,
    children: items,
    addEventListener: (type, fn) => { if (type === 'keydown') keydown = fn; },
    querySelectorAll: () => items,
  };
  return { el, fire: (ev) => keydown && keydown(ev), items };
}

// Drives a keydown event object against the captured handler.
function keyEvent(key, target, extra = {}) {
  return { key, target, preventDefault: vi.fn(), stopPropagation: vi.fn(), ...extra };
}

beforeEach(() => {
  // MutationObserver is referenced at wiring time; a no-op double suffices.
  global.MutationObserver = class { observe() {} disconnect() {} };
  // Deterministic rAF for announce().
  global.requestAnimationFrame = (cb) => { cb(); return 1; };
});

describe('makeListNavigable — roving tabindex', () => {
  it('makes exactly one item the tab stop (the active row) and demotes the rest', () => {
    const items = [mkItem(), mkItem({ active: true }), mkItem()];
    const { el } = mkList(items);
    makeListNavigable(el);
    expect(items.map(i => i.tabIndex)).toEqual([-1, 0, -1]);
  });

  it('falls back to the first item when none is active', () => {
    const items = [mkItem(), mkItem(), mkItem()];
    const { el } = mkList(items);
    makeListNavigable(el);
    expect(items.map(i => i.tabIndex)).toEqual([0, -1, -1]);
  });

  it('demotes nested buttons to tabindex -1 so the list is a single tab stop', () => {
    const items = [mkItem({ buttons: 2 })];
    const { el } = mkList(items);
    makeListNavigable(el);
    expect(items[0]._kids.every(b => b.tabIndex === -1)).toBe(true);
  });

  it('is idempotent — a second call on the same list is a no-op', () => {
    const items = [mkItem()];
    const { el } = mkList(items);
    let added = 0;
    el.addEventListener = (t) => { if (t === 'keydown') added++; };
    makeListNavigable(el);
    makeListNavigable(el);
    expect(added).toBe(1);
  });

  it('ignores a null list without throwing', () => {
    expect(() => makeListNavigable(null)).not.toThrow();
  });
});

describe('makeListNavigable — keyboard movement', () => {
  it('ArrowDown moves focus to the next row', () => {
    const items = [mkItem(), mkItem(), mkItem()];
    const { el, fire } = mkList(items);
    makeListNavigable(el);
    fire(keyEvent('ArrowDown', items[0]));
    expect(items[1]._focused).toBe(1);
    expect(items[1].tabIndex).toBe(0);
    expect(items[0].tabIndex).toBe(-1);
  });

  it('ArrowUp at the top clamps to the first row', () => {
    const items = [mkItem(), mkItem()];
    const { el, fire } = mkList(items);
    makeListNavigable(el);
    fire(keyEvent('ArrowUp', items[0]));
    expect(items[0]._focused).toBe(1);
  });

  it('End jumps to the last row, Home to the first', () => {
    const items = [mkItem(), mkItem(), mkItem()];
    const { el, fire } = mkList(items);
    makeListNavigable(el);
    fire(keyEvent('End', items[0]));
    expect(items[2]._focused).toBe(1);
    fire(keyEvent('Home', items[2]));
    expect(items[0]._focused).toBe(1);
  });

  it('Enter activates via onActivate; Space too', () => {
    const items = [mkItem(), mkItem()];
    const { el, fire } = mkList(items);
    const onActivate = vi.fn();
    makeListNavigable(el, { onActivate });
    fire(keyEvent('Enter', items[1]));
    fire(keyEvent(' ', items[0]));
    expect(onActivate).toHaveBeenCalledTimes(2);
    expect(onActivate).toHaveBeenNthCalledWith(1, items[1]);
    expect(onActivate).toHaveBeenNthCalledWith(2, items[0]);
  });

  it('Enter falls back to click() when no onActivate is given', () => {
    const items = [mkItem()];
    const { el, fire } = mkList(items);
    makeListNavigable(el);
    fire(keyEvent('Enter', items[0]));
    expect(items[0].click).toHaveBeenCalledOnce();
  });

  it('Delete triggers onDelete only when provided', () => {
    const items = [mkItem()];
    const { el, fire } = mkList(items);
    const onDelete = vi.fn();
    makeListNavigable(el, { onDelete });
    fire(keyEvent('Delete', items[0]));
    expect(onDelete).toHaveBeenCalledWith(items[0]);
  });

  it('Shift+F10 and ContextMenu open the actions menu', () => {
    const items = [mkItem()];
    const { el, fire } = mkList(items);
    const onContextMenu = vi.fn();
    makeListNavigable(el, { onContextMenu });
    fire(keyEvent('F10', items[0], { shiftKey: true }));
    fire(keyEvent('ContextMenu', items[0]));
    expect(onContextMenu).toHaveBeenCalledTimes(2);
  });

  it('ignores keys whose target is not a row', () => {
    const items = [mkItem()];
    const { el, fire } = mkList(items);
    const onActivate = vi.fn();
    makeListNavigable(el, { onActivate });
    fire(keyEvent('Enter', { tagName: 'DIV' }));
    expect(onActivate).not.toHaveBeenCalled();
  });
});

describe('announce', () => {
  it('is a harmless no-op when createElement yields a non-element stub', () => {
    global.document = {
      createElement: () => ({}),          // no setAttribute → region is null
      body: { appendChild: vi.fn() },
    };
    expect(() => announce('hello')).not.toThrow();
  });

  it('ignores empty messages', () => {
    const appendChild = vi.fn();
    global.document = { createElement: () => realEl(), body: { appendChild } };
    announce('');
    expect(appendChild).not.toHaveBeenCalled();
  });

  it('writes the message into a polite live region on the next frame', () => {
    const region = realEl();
    global.document = {
      createElement: () => (region._used ? realEl() : ((region._used = true), region)),
      body: { appendChild: vi.fn() },
    };
    announce('Reconnected to the gateway.');
    expect(region.textContent).toBe('Reconnected to the gateway.');
    expect(region.getAttribute('aria-live')).toBe('polite');
    expect(region.getAttribute('role')).toBe('status');
  });
});

// A richer element double that supports the attribute surface announce() uses.
function realEl() {
  const attrs = {};
  return {
    textContent: '',
    className: '',
    setAttribute(k, v) { attrs[k] = v; },
    getAttribute(k) { return attrs[k]; },
  };
}
