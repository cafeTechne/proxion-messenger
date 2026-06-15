import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createNotifications } from './notifications.js';

// Minimal DOM + Notification/window stubs.
let container;
beforeEach(() => {
  const children = [];
  container = {
    children,
    appendChild: (el) => children.push(el),
  };
  global.document = {
    getElementById: (id) => (id === 'toast-container' ? container : null),
    createElement: () => ({ style: {}, textContent: '', remove() {} }),
    hasFocus: () => false,
  };
  global.window = {};
  vi.useFakeTimers();
});

function make(soundEnabled = true) {
  return createNotifications({ getSoundEnabled: () => soundEnabled });
}

describe('showToast', () => {
  it('appends a toast element to the container', () => {
    const { showToast } = make();
    showToast('hello');
    expect(container.children).toHaveLength(1);
    expect(container.children[0].textContent).toBe('hello');
  });
  it('is a no-op when the container is missing', () => {
    global.document.getElementById = () => null;
    const { showToast } = make();
    expect(() => showToast('hi')).not.toThrow();
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
  it('respects the soundEnabled gate for web notifications', () => {
    const ctor = vi.fn();
    global.Notification = Object.assign(ctor, { permission: 'granted' });
    global.window = { Notification: global.Notification };
    const { showOsNotification } = make(false); // sound off → suppressed
    showOsNotification('a', 'b', 'c');
    expect(ctor).not.toHaveBeenCalled();
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
