import { describe, it, expect, vi } from 'vitest';
import { topmostDialog, closeTopmostDialog } from './focus-trap.js';

// Minimal fake DOM: each node has style.display / style.zIndex, a parent
// chain, attributes, and children. getStyle reads the inline style, which is
// what the real getComputedStyle reflects for these overlays.
function node({ display = 'flex', zIndex = '', attrs = {}, children = [], onClick } = {}) {
  const n = {
    style: { display, zIndex }, hidden: false, parentElement: null, attrs, children,
    hasAttribute: (k) => k in attrs,
    click: vi.fn(() => onClick && onClick(n)),
  };
  children.forEach((c) => { c.parentElement = n; });
  return n;
}
function all(n) { return n.children.flatMap((c) => [c, ...all(c)]); }
function root(dialogs) {
  const body = node({ children: dialogs });
  return {
    querySelectorAll: () => dialogs,
    body,
  };
}
const getStyle = (el) => el.style;
// querySelectorAll on a dialog: return its [data-modal-cancel] descendants.
function withCancelQuery(d) {
  d.querySelectorAll = () => all(d).filter((c) => 'data-modal-cancel' in c.attrs);
  return d;
}

describe('topmostDialog', () => {
  it('picks the highest z-index among visible dialogs', () => {
    const settings = withCancelQuery(node({ zIndex: '1002' }));
    const confirm = withCancelQuery(node({ zIndex: '1400' }));
    const hidden = withCancelQuery(node({ zIndex: '5000', display: 'none' }));
    expect(topmostDialog(root([settings, confirm, hidden]), getStyle)).toBe(confirm);
  });
  it('breaks z-index ties by later DOM order', () => {
    const a = withCancelQuery(node({ zIndex: '1002' }));
    const b = withCancelQuery(node({ zIndex: '1002' }));
    expect(topmostDialog(root([a, b]), getStyle)).toBe(b);
  });
  it('returns null when nothing is open', () => {
    const a = withCancelQuery(node({ display: 'none' }));
    expect(topmostDialog(root([a]), getStyle)).toBeNull();
  });
});

describe('closeTopmostDialog', () => {
  it('closes only the top dialog, via its cancel control', () => {
    const settingsCancel = node({ display: 'inline-block', attrs: { 'data-modal-cancel': '' } });
    const settings = withCancelQuery(node({ zIndex: '1002', children: [settingsCancel] }));
    const confirmCancel = node({
      display: 'inline-block', attrs: { 'data-modal-cancel': '' },
      onClick: () => { confirm.style.display = 'none'; },
    });
    const confirm = withCancelQuery(node({ zIndex: '1400', children: [confirmCancel] }));
    const closed = closeTopmostDialog(root([settings, confirm]), getStyle);
    expect(closed).toBe(confirm);
    expect(confirmCancel.click).toHaveBeenCalledOnce();
    expect(settingsCancel.click).not.toHaveBeenCalled();
    expect(settings.style.display).toBe('flex');
  });
  it('skips a cancel control inside a hidden section and uses the visible one', () => {
    const cancel = node({ display: 'inline-block', attrs: { 'data-modal-cancel': '' } });
    const form = node({ display: 'none', children: [cancel] });
    const done = node({ display: 'inline-block', attrs: { 'data-modal-cancel': '' } });
    const result = node({ display: 'block', children: [done] });
    const d = withCancelQuery(node({ zIndex: '1002', children: [form, result] }));
    closeTopmostDialog(root([d]), getStyle);
    expect(cancel.click).not.toHaveBeenCalled();
    expect(done.click).toHaveBeenCalledOnce();
  });
  it('falls back to hiding a dialog with no cancel control', () => {
    const d = withCancelQuery(node({ zIndex: '1002' }));
    expect(closeTopmostDialog(root([d]), getStyle)).toBe(d);
    expect(d.style.display).toBe('none');
  });
  it('never closes a persistent gate', () => {
    const gate = withCancelQuery(node({ zIndex: '1002', attrs: { 'data-modal-persistent': '' } }));
    expect(closeTopmostDialog(root([gate]), getStyle)).toBeNull();
    expect(gate.style.display).toBe('flex');
  });
  it('closes a dialog stacked over a persistent gate but leaves the gate', () => {
    const gate = withCancelQuery(node({ zIndex: '1002', attrs: { 'data-modal-persistent': '' } }));
    const prompt = withCancelQuery(node({ zIndex: '3000' }));
    expect(closeTopmostDialog(root([gate, prompt]), getStyle)).toBe(prompt);
    expect(prompt.style.display).toBe('none');
    expect(gate.style.display).toBe('flex');
  });
  it('returns null when no dialog is open', () => {
    expect(closeTopmostDialog(root([]), getStyle)).toBeNull();
  });
});
