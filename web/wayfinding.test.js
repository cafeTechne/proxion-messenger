import { describe, it, expect, vi } from 'vitest';
import { buildPageTitle, viewLabel, createFaviconBadge, jumpToMessage } from './wayfinding.js';

// Stand-in for t('page.titleView', { view }) with the English string.
const tr = (key, vars) => (key === 'page.titleView' ? `${vars.view} · Proxion` : key);

describe('viewLabel', () => {
  it('prefixes rooms with # and leaves DMs bare', () => {
    expect(viewLabel({ type: 'room', name: 'general' })).toBe('#general');
    expect(viewLabel({ type: 'local_room', name: 'lan' })).toBe('#lan');
    expect(viewLabel({ type: 'dm', name: 'Alice' })).toBe('Alice');
    expect(viewLabel({ type: 'local_dm', name: 'Bob' })).toBe('Bob');
  });
  it('is empty with no view or no name', () => {
    expect(viewLabel(null)).toBe('');
    expect(viewLabel({ type: 'room' })).toBe('');
  });
});

describe('buildPageTitle', () => {
  it('names the open room or DM', () => {
    expect(buildPageTitle(0, { type: 'room', name: 'general' }, tr)).toBe('#general · Proxion');
    expect(buildPageTitle(0, { type: 'dm', name: 'Alice' }, tr)).toBe('Alice · Proxion');
  });
  it('leads with the unread count', () => {
    expect(buildPageTitle(3, { type: 'room', name: 'general' }, tr)).toBe('(3) #general · Proxion');
    expect(buildPageTitle(12, { type: 'dm', name: 'Alice' }, tr)).toBe('(12) Alice · Proxion');
  });
  it('falls back to the app name when nothing is open', () => {
    expect(buildPageTitle(0, null, tr)).toBe('Proxion');
    expect(buildPageTitle(2, null, tr)).toBe('(2) Proxion');
  });
  it('never uses an em dash', () => {
    expect(buildPageTitle(1, { type: 'room', name: 'x' }, tr)).not.toMatch(/—/);
  });
});

describe('jumpToMessage', () => {
  function row(tabIndex = -1) {
    const cls = new Set();
    return {
      tabIndex, offsetWidth: 1, parentElement: {},
      classList: { add: (c) => cls.add(c), remove: (c) => cls.delete(c), has: (c) => cls.has(c) },
      scrollIntoView: vi.fn(), focus: vi.fn(),
    };
  }
  it('scrolls once, focuses without scrolling again, highlights, and takes the roving tab stop', () => {
    vi.useFakeTimers();
    try {
      const prev = row(0);
      const target = row(-1);
      const feed = { querySelectorAll: () => [prev, target] };
      target.closest = () => feed;
      expect(jumpToMessage(target, 'smooth')).toBe(true);
      expect(target.scrollIntoView).toHaveBeenCalledOnce();
      expect(target.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' });
      expect(target.focus).toHaveBeenCalledWith({ preventScroll: true });
      expect(target.tabIndex).toBe(0);
      expect(prev.tabIndex).toBe(-1);
      expect(target.classList.has('message--jumped')).toBe(true);
      vi.advanceTimersByTime(2000);
      expect(target.classList.has('message--jumped')).toBe(false);
    } finally { vi.useRealTimers(); }
  });
  it('is a no-op for a message that is not loaded', () => {
    expect(jumpToMessage(null)).toBe(false);
  });
});

describe('createFaviconBadge', () => {
  it('is a no-op without an icon link', () => {
    const b = createFaviconBadge({ querySelector: () => null });
    expect(() => b.update(true)).not.toThrow();
  });
  it('restores the original icon when the dot is turned off', () => {
    const attrs = { href: 'icons/icon-192.svg', type: 'image/svg+xml' };
    const link = {
      getAttribute: (k) => attrs[k] ?? null,
      setAttribute: vi.fn((k, v) => { attrs[k] = v; }),
      removeAttribute: (k) => { delete attrs[k]; },
    };
    const b = createFaviconBadge({ querySelector: () => link });
    attrs.href = 'data:image/png;base64,xyz';
    attrs.type = 'image/png';
    b.update(false);
    expect(attrs.href).toBe('icons/icon-192.svg');
    expect(attrs.type).toBe('image/svg+xml');
  });
});
