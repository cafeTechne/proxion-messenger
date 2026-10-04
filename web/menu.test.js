import { describe, it, expect, vi, beforeEach } from 'vitest';
import { menuKeyAction, clampMenuPosition, openMenu, closeMenu, menuItems } from './menu.js';

describe('menuKeyAction', () => {
  it('ArrowDown/ArrowUp move and wrap', () => {
    expect(menuKeyAction('ArrowDown', 0, 3)).toEqual({ focus: 1 });
    expect(menuKeyAction('ArrowDown', 2, 3)).toEqual({ focus: 0 });
    expect(menuKeyAction('ArrowUp', 0, 3)).toEqual({ focus: 2 });
    expect(menuKeyAction('ArrowUp', 2, 3)).toEqual({ focus: 1 });
  });
  it('with no focused item, ArrowDown starts at the top and ArrowUp at the bottom', () => {
    expect(menuKeyAction('ArrowDown', -1, 4)).toEqual({ focus: 0 });
    expect(menuKeyAction('ArrowUp', -1, 4)).toEqual({ focus: 3 });
  });
  it('Home/End jump to the ends', () => {
    expect(menuKeyAction('Home', 2, 5)).toEqual({ focus: 0 });
    expect(menuKeyAction('End', 0, 5)).toEqual({ focus: 4 });
  });
  it('Escape and Tab close', () => {
    expect(menuKeyAction('Escape', 1, 3)).toEqual({ close: true });
    expect(menuKeyAction('Tab', 1, 3)).toEqual({ close: true });
    expect(menuKeyAction('Escape', -1, 0)).toEqual({ close: true });
  });
  it('other keys are left alone', () => {
    expect(menuKeyAction('a', 0, 3)).toBeNull();
    expect(menuKeyAction('Enter', 0, 3)).toBeNull();
    expect(menuKeyAction('ArrowDown', -1, 0)).toBeNull();
  });
});

describe('clampMenuPosition', () => {
  it('keeps the requested point when the menu fits', () => {
    expect(clampMenuPosition(100, 100, 180, 120, 1440, 900)).toEqual({ left: 100, top: 100 });
  });
  it('pulls the menu back inside the right and bottom edges', () => {
    expect(clampMenuPosition(1400, 880, 180, 120, 1440, 900)).toEqual({ left: 1252, top: 772 });
  });
  it('never goes past the top-left margin', () => {
    expect(clampMenuPosition(-50, -10, 180, 120, 1440, 900)).toEqual({ left: 8, top: 8 });
  });
});

// ---- DOM wiring, on a minimal fake ----
function fakeButton(label, { hidden = false } = {}) {
  const attrs = {};
  return {
    label, disabled: false, hidden: false, tabIndex: 0,
    style: { display: hidden ? 'none' : '' },
    setAttribute: (k, v) => { attrs[k] = v; }, getAttribute: (k) => attrs[k],
    attrs,
    focus() { doc.activeElement = this; },
  };
}
let doc;
function fakeMenu(buttons) {
  const listeners = {};
  const attrs = {};
  const menu = {
    style: { display: 'none' }, ownerDocument: null, attrs,
    setAttribute: (k, v) => { attrs[k] = v; },
    querySelectorAll: (sel) => (sel === 'button' ? buttons : []),
    addEventListener: (ev, fn) => { (listeners[ev] ||= []).push(fn); },
    getBoundingClientRect: () => ({ width: 180, height: 100 }),
    contains: (el) => buttons.includes(el),
    fire(ev, e) { (listeners[ev] || []).forEach((fn) => fn(e)); },
  };
  menu.ownerDocument = doc;
  return menu;
}

beforeEach(() => {
  doc = { activeElement: null, contains: () => true };
  global.window = { innerWidth: 1000, innerHeight: 600 };
  global.requestAnimationFrame = (cb) => cb();
});

describe('openMenu / closeMenu', () => {
  it('applies menu roles, positions inside the viewport and focuses the first shown item', () => {
    const hiddenFirst = fakeButton('edit', { hidden: true });
    const reply = fakeButton('reply');
    const copy = fakeButton('copy');
    const menu = fakeMenu([hiddenFirst, reply, copy]);
    openMenu(menu, { x: 950, y: 580 });
    expect(menu.attrs.role).toBe('menu');
    expect(reply.attrs.role).toBe('menuitem');
    expect(hiddenFirst.attrs.role).toBe('menuitem');
    expect(menu.style.display).toBe('block');
    expect(menu.style.left).toBe('812px');
    expect(menu.style.top).toBe('492px');
    expect(doc.activeElement).toBe(reply);
    expect(menuItems(menu)).toEqual([reply, copy]);
  });

  it('arrow keys move focus and skip hidden items', () => {
    const a = fakeButton('a'); const h = fakeButton('h', { hidden: true }); const b = fakeButton('b');
    const menu = fakeMenu([a, h, b]);
    openMenu(menu, { x: 0, y: 0 });
    const e = { key: 'ArrowDown', preventDefault: vi.fn(), stopPropagation: vi.fn() };
    menu.fire('keydown', e);
    expect(doc.activeElement).toBe(b);
    expect(e.preventDefault).toHaveBeenCalled();
    menu.fire('keydown', { key: 'End', preventDefault() {}, stopPropagation() {} });
    expect(doc.activeElement).toBe(b);
    menu.fire('keydown', { key: 'Home', preventDefault() {}, stopPropagation() {} });
    expect(doc.activeElement).toBe(a);
  });

  it('Escape closes and returns focus to the opener', () => {
    const opener = { focus: vi.fn() };
    const a = fakeButton('a');
    const menu = fakeMenu([a]);
    const onClose = vi.fn();
    openMenu(menu, { x: 0, y: 0, opener, onClose });
    menu.fire('keydown', { key: 'Escape', preventDefault() {}, stopPropagation() {} });
    expect(menu.style.display).toBe('none');
    expect(opener.focus).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('focus leaving the menu closes it without stealing focus back', () => {
    const opener = { focus: vi.fn() };
    const a = fakeButton('a');
    const menu = fakeMenu([a]);
    openMenu(menu, { x: 0, y: 0, opener });
    menu.fire('focusout', { relatedTarget: { outside: true } });
    expect(menu.style.display).toBe('none');
    expect(opener.focus).not.toHaveBeenCalled();
  });

  it('closeMenu on a closed menu is a no-op', () => {
    const menu = fakeMenu([fakeButton('a')]);
    expect(closeMenu(menu)).toBe(false);
  });
});
