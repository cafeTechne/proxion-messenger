import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createNotifications } from './notifications.js';

// Minimal DOM + Notification/window stubs (the suite runs under node, no jsdom).
function mkEl(tag = 'div') {
  const el = {
    tagName: tag.toUpperCase(),
    children: [],
    attrs: {},
    listeners: {},
    classes: new Set(),
    style: {},
    textContent: '',
    innerHTML: '',
    title: '',
    hidden: false,
    removed: false,
    get className() { return [...el.classes].join(' '); },
    set className(v) { el.classes = new Set(String(v).split(/\s+/).filter(Boolean)); },
    classList: {
      add: (c) => el.classes.add(c),
      remove: (c) => el.classes.delete(c),
      contains: (c) => el.classes.has(c),
    },
    setAttribute(k, v) { el.attrs[k] = String(v); },
    getAttribute(k) { return k in el.attrs ? el.attrs[k] : null; },
    appendChild(c) { el.children.push(c); c.parent = el; return c; },
    addEventListener(type, fn) { (el.listeners[type] ||= []).push(fn); },
    fire(type, ev = {}) { (el.listeners[type] || []).forEach(fn => fn({ stopPropagation() {}, ...ev })); },
    remove() {
      el.removed = true;
      if (el.parent) el.parent.children = el.parent.children.filter(c => c !== el);
    },
  };
  return el;
}

let container;
beforeEach(() => {
  container = mkEl();
  global.document = {
    getElementById: (id) => (id === 'toast-container' ? container : null),
    createElement: (tag) => mkEl(tag),
    hasFocus: () => false,
    body: mkEl('body'),   // a11y.announce() appends its live regions here
  };
  global.window = {};
  global.requestAnimationFrame = (cb) => { cb(); return 1; };
  vi.useFakeTimers();
});

function make(soundEnabled = true, desktopNotifEnabled = true) {
  return createNotifications({
    getSoundEnabled: () => soundEnabled,
    getDesktopNotifEnabled: () => desktopNotifEnabled,
  });
}

const msgOf = (toast) => toast.children.find(c => c.classes.has('toast__msg'));
const countOf = (toast) => toast.children.find(c => c.classes.has('toast__count'));
const closeOf = (toast) => toast.children.find(c => c.classes.has('toast__close'));

describe('showToast', () => {
  it('appends a toast element with its message', () => {
    const { showToast } = make();
    showToast('hello');
    expect(container.children).toHaveLength(1);
    expect(msgOf(container.children[0]).textContent).toBe('hello');
  });

  it('is a no-op when the container is missing', () => {
    global.document.getElementById = () => null;
    const { showToast } = make();
    expect(() => showToast('hi')).not.toThrow();
  });

  it('uses a type class instead of inline colors', () => {
    const { showToast } = make();
    const kinds = [];
    for (const [m, type] of [['ok', 'success'], ['bad', 'error'], ['hm', 'warning'], ['fyi', undefined]]) {
      showToast(m, type);
      const last = container.children[container.children.length - 1];
      kinds.push(last.className);
      expect(last.style.background).toBeUndefined();
    }
    expect(kinds).toEqual(['toast toast--success', 'toast toast--error', 'toast toast--warning', 'toast toast--info']);
  });

  it('caps the stack at 3, dropping the oldest', () => {
    const { showToast } = make();
    ['a', 'b', 'c', 'd'].forEach(m => showToast(m));
    expect(container.children.map(c => msgOf(c).textContent)).toEqual(['b', 'c', 'd']);
  });

  it('collapses a repeat into one toast with a count', () => {
    const { showToast } = make();
    showToast('Saved', 'success');
    showToast('Saved', 'success');
    expect(container.children).toHaveLength(1);
    const count = countOf(container.children[0]);
    expect(count.hidden).toBe(false);
    expect(count.textContent).toBe('toast.count'); // i18n key in the test env
  });

  it('does not collapse the same text with a different type', () => {
    const { showToast } = make();
    showToast('x', 'error');
    showToast('x', 'success');
    expect(container.children).toHaveLength(2);
  });

  it('has a labelled close button that dismisses it', () => {
    const { showToast } = make();
    showToast('bye');
    const toast = container.children[0];
    const close = closeOf(toast);
    expect(close.tagName).toBe('BUTTON');
    expect(close.attrs['aria-label']).toBe('toast.dismiss');
    expect(close.innerHTML).toContain('<svg');
    close.fire('click');
    vi.advanceTimersByTime(400);
    expect(container.children).toHaveLength(0);
  });

  it('times out ordinary toasts', () => {
    const { showToast } = make();
    showToast('short');
    vi.advanceTimersByTime(3500 + 400);
    expect(container.children).toHaveLength(0);
  });

  it('keeps an error toast with an action until it is dismissed', () => {
    const onClick = vi.fn();
    const { showToast } = make();
    showToast('failed', 'error', { action: { label: 'Retry', onClick } });
    vi.advanceTimersByTime(60000);
    expect(container.children).toHaveLength(1);
    const action = container.children[0].children.find(c => c.classes.has('toast__action'));
    action.fire('click');
    expect(onClick).toHaveBeenCalled();
    vi.advanceTimersByTime(400);
    expect(container.children).toHaveLength(0);
  });

  it('a toast that dropped off the cap can be shown again', () => {
    const { showToast } = make();
    ['a', 'b', 'c', 'd'].forEach(m => showToast(m));
    showToast('a');
    expect(container.children.map(c => msgOf(c).textContent)).toEqual(['c', 'd', 'a']);
  });
});

describe('showOsNotification', () => {
  it('prefers the Tauri invoke bridge when present', () => {
    const invoke = vi.fn(() => Promise.resolve());
    global.window = { __TAURI__: { invoke } };
    const { showOsNotification } = make();
    showOsNotification('Title', 'Body', 't1');
    expect(invoke).toHaveBeenCalledWith('show_notification', { title: 'Title', body: 'Body' });
  });
  it('does nothing without Notification support', () => {
    global.window = {};
    const { showOsNotification } = make();
    expect(() => showOsNotification('a', 'b', 'c')).not.toThrow();
  });
  it('shows a web notification when sound is OFF but desktop notifications are ON', () => {
    const ctor = vi.fn(function () { this.close = () => {}; });
    global.Notification = Object.assign(ctor, { permission: 'granted' });
    global.window = { Notification: global.Notification };
    const { showOsNotification } = make(false, true); // sound off, desktop on
    showOsNotification('a', 'b', 'c');
    expect(ctor).toHaveBeenCalled(); // no longer conflated with the sound setting
  });
  it('suppresses web notifications when desktop notifications are OFF', () => {
    const ctor = vi.fn();
    global.Notification = Object.assign(ctor, { permission: 'granted' });
    global.window = { Notification: global.Notification };
    const { showOsNotification } = make(true, false); // sound on, desktop off
    showOsNotification('a', 'b', 'c');
    expect(ctor).not.toHaveBeenCalled();
  });
  it('suppresses the Tauri bridge when desktop notifications are OFF', () => {
    const invoke = vi.fn(() => Promise.resolve());
    global.window = { __TAURI__: { invoke } };
    const { showOsNotification } = make(true, false);
    showOsNotification('Title', 'Body', 't1');
    expect(invoke).not.toHaveBeenCalled();
  });
});

describe('playNotificationSound', () => {
  it('is a no-op when sound is disabled', () => {
    const AudioContext = vi.fn();
    global.window = { AudioContext };
    const { playNotificationSound } = make(false);
    playNotificationSound();
    expect(AudioContext).not.toHaveBeenCalled();
  });
});

describe('requestNotifPermission', () => {
  it('requests permission when default', () => {
    const requestPermission = vi.fn();
    global.window = {};
    global.Notification = { permission: 'default', requestPermission };
    global.window.Notification = global.Notification;
    const { requestNotifPermission } = make();
    requestNotifPermission();
    expect(requestPermission).toHaveBeenCalled();
  });
});
