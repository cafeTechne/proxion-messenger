import { describe, it, expect, vi } from 'vitest';
import { topmostDialog, closeTopmostDialog, syncBackgroundInert, closeDialog, initialFocusTarget, isVisible } from './focus-trap.js';

describe('initialFocusTarget', () => {
  const ctl = (attrs = {}) => ({ hasAttribute: (k) => k in attrs });
  it('prefers a control marked data-autofocus over the first one', () => {
    const first = ctl(), primary = ctl({ 'data-autofocus': '' });
    expect(initialFocusTarget([first, ctl(), primary])).toBe(primary);
  });
  it('falls back to the first control, or null when there is none', () => {
    const first = ctl();
    expect(initialFocusTarget([first, ctl()])).toBe(first);
    expect(initialFocusTarget([])).toBe(null);
  });
});

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

// Fake element tree for the inert sync: children, parentElement, attributes,
// a tiny matches() for the selectors KEEP_LIVE_SEL uses, and querySelectorAll
// for the marker attribute.
function el(id, { attrs = {}, children = [] } = {}) {
  const n = {
    id, attrs: { ...attrs }, children, parentElement: null, inert: false,
    hasAttribute: (k) => k in n.attrs,
    getAttribute: (k) => n.attrs[k],
    setAttribute: (k, v) => { n.attrs[k] = v; },
    removeAttribute: (k) => { delete n.attrs[k]; },
    matches: (sel) => (n.id === 'toast-container' && sel.includes('#toast-container'))
      || ('aria-live' in n.attrs && sel.includes('[aria-live]'))
      || (['status', 'alert', 'log'].includes(n.attrs.role) && sel.includes(`[role="${n.attrs.role}"]`)),
    querySelectorAll: (sel) => {
      const k = sel.slice(1, -1);
      const out = [];
      const walk = (x) => x.children.forEach((c) => { if (k in c.attrs) out.push(c); walk(c); });
      walk(n);
      return out;
    },
  };
  children.forEach((c) => { c.parentElement = n; });
  return n;
}
function page() {
  const meme = el('meme-modal');
  const feed = el('feed');
  const app = el('app', { children: [feed, meme] });
  const toasts = el('toast-container');
  const live = el('polite', { attrs: { 'aria-live': 'polite', role: 'status' } });
  const settings = el('settings-modal');
  const confirm = el('confirm-modal');
  const banners = el('banner-slot');
  const preInert = el('already-inert');
  preInert.inert = true;
  const body = el('body', { children: [banners, app, toasts, live, settings, confirm, preInert] });
  return { body, app, feed, meme, toasts, live, settings, confirm, banners, preInert };
}

describe('syncBackgroundInert', () => {
  it('makes the page inert behind an open dialog but keeps toasts and live regions usable', () => {
    const p = page();
    syncBackgroundInert(p.body, p.settings);
    expect(p.app.inert).toBe(true);
    expect(p.banners.inert).toBe(true);
    expect(p.confirm.inert).toBe(true);
    expect(p.settings.inert).toBe(false);
    expect(p.toasts.inert).toBe(false);
    expect(p.live.inert).toBe(false);
  });
  it('makes a dialog stacked under the top one inert, and releases it when the top closes', () => {
    const p = page();
    syncBackgroundInert(p.body, p.settings);
    syncBackgroundInert(p.body, p.confirm);
    expect(p.settings.inert).toBe(true);
    expect(p.confirm.inert).toBe(false);
    syncBackgroundInert(p.body, p.settings);
    expect(p.settings.inert).toBe(false);
    expect(p.app.inert).toBe(true);
  });
  it('handles a dialog nested inside #app: its siblings go inert, its ancestors do not', () => {
    const p = page();
    syncBackgroundInert(p.body, p.meme);
    expect(p.app.inert).toBe(false);
    expect(p.feed.inert).toBe(true);
    expect(p.meme.inert).toBe(false);
    expect(p.banners.inert).toBe(true);
  });
  it('clears everything it set when the last dialog closes, and nothing else', () => {
    const p = page();
    syncBackgroundInert(p.body, p.settings);
    syncBackgroundInert(p.body, null);
    expect(p.app.inert).toBe(false);
    expect(p.banners.inert).toBe(false);
    expect(p.confirm.inert).toBe(false);
    expect('data-trap-inert' in p.app.attrs).toBe(false);
    // Inert before any dialog opened, so not ours to release.
    expect(p.preInert.inert).toBe(true);
  });
});

describe('closeDialog', () => {
  it('hides the dialog (focus restore runs first once the trap is installed)', () => {
    const d = { style: { display: 'flex' } };
    closeDialog(d);
    expect(d.style.display).toBe('none');
    expect(() => closeDialog(null)).not.toThrow();
  });
});

// The confirm dialog's "also delete" checkbox sits in a <label hidden>. Its own
// computed display is inline-block, so the old check counted it as the trap's
// first stop: focus went to <body> on open and Tab never reached Confirm.
describe('isVisible', () => {
  const el = ({ display = 'block', visibility = 'visible', hidden = false, inert = false, parent = null } = {}) =>
    ({ nodeType: 1, isConnected: true, hidden, inert, parentElement: parent, _s: { display, visibility } });
  const gs = (n) => n._s;
  const dialog = () => el({ display: 'flex' });

  it('a control in a shown dialog is visible', () => {
    expect(isVisible(el({ parent: dialog() }), gs)).toBe(true);
  });
  it('a checkbox inside a hidden label is not', () => {
    const label = el({ display: 'none', hidden: true, parent: dialog() });
    expect(isVisible(el({ display: 'inline-block', parent: label }), gs)).toBe(false);
  });
  it('the hidden attribute counts even when CSS overrides its display', () => {
    const label = el({ display: 'flex', hidden: true, parent: dialog() });
    expect(isVisible(el({ parent: label }), gs)).toBe(false);
  });
  it('any display:none ancestor hides the control', () => {
    const wrap = el({ display: 'none', parent: el({ parent: dialog() }) });
    expect(isVisible(el({ parent: wrap }), gs)).toBe(false);
  });
  it('visibility hidden or collapse hides the control', () => {
    expect(isVisible(el({ visibility: 'hidden', parent: dialog() }), gs)).toBe(false);
    expect(isVisible(el({ visibility: 'collapse', parent: dialog() }), gs)).toBe(false);
  });
  it('an inert ancestor hides the control unless inert checks are off', () => {
    const d = el({ display: 'flex', inert: true });
    expect(isVisible(el({ parent: d }), gs)).toBe(false);
    expect(isVisible(d, gs, { inert: false })).toBe(true);
  });
  it('a detached node is not visible', () => {
    expect(isVisible({ ...el(), isConnected: false }, gs)).toBe(false);
    expect(isVisible(null, gs)).toBe(false);
  });
});
