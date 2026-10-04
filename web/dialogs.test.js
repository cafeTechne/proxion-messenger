import { describe, it, expect, vi, beforeEach } from 'vitest';
import { showConfirm, cancelDialog, installDialogDismiss, fieldInvalidState } from './dialogs.js';

// i18n's t() returns the key itself when no locale is loaded.

function classList() {
  const set = new Set();
  return {
    toggle: (c, on) => { if (on) set.add(c); else set.delete(c); },
    contains: (c) => set.has(c),
    add: (c) => set.add(c),
  };
}
function el(over = {}) {
  return { style: {}, textContent: '', hidden: false, disabled: false, checked: false, classList: classList(), ...over };
}

let els;
let doc;
beforeEach(() => {
  els = {
    'confirm-modal': el({ style: { display: 'none' } }),
    'confirm-title': el(),
    'confirm-msg': el(),
    'confirm-ok': el(),
    'confirm-cancel': el(),
    'confirm-check-row': el({ hidden: true }),
    'confirm-check': el(),
    'confirm-check-label': el(),
  };
  doc = { getElementById: (id) => els[id] || null };
});

describe('showConfirm', () => {
  it('fills title, message and verb, and opens', () => {
    showConfirm('Delete this room permanently?', () => {}, null,
      { title: 'Delete room?', confirmLabel: 'Delete room' }, doc);
    expect(els['confirm-title'].textContent).toBe('Delete room?');
    expect(els['confirm-msg'].textContent).toBe('Delete this room permanently?');
    expect(els['confirm-ok'].textContent).toBe('Delete room');
    expect(els['confirm-modal'].style.display).toBe('flex');
  });

  it('falls back to a generic title and Confirm/Cancel labels', () => {
    showConfirm('Sure?', () => {}, null, undefined, doc);
    expect(els['confirm-title'].textContent).toBe('confirm.title');
    expect(els['confirm-ok'].textContent).toBe('btn.confirm');
    expect(els['confirm-cancel'].textContent).toBe('btn.cancel');
  });

  it('danger swaps the accent button for the solid danger variant, and back', () => {
    showConfirm('x', () => {}, null, { danger: true }, doc);
    expect(els['confirm-ok'].classList.contains('btn--danger')).toBe(true);
    expect(els['confirm-ok'].classList.contains('btn--accent')).toBe(false);
    showConfirm('y', () => {}, null, {}, doc);
    expect(els['confirm-ok'].classList.contains('btn--danger')).toBe(false);
    expect(els['confirm-ok'].classList.contains('btn--accent')).toBe(true);
  });

  it('runs onConfirm once and closes on OK; runs onCancel on Cancel', () => {
    const ok = vi.fn(); const cancel = vi.fn();
    showConfirm('x', ok, cancel, {}, doc);
    els['confirm-ok'].onclick();
    expect(ok).toHaveBeenCalledTimes(1);
    expect(els['confirm-modal'].style.display).toBe('none');
    showConfirm('x', ok, cancel, {}, doc);
    els['confirm-cancel'].onclick();
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(ok).toHaveBeenCalledTimes(1);
  });

  it('a checkLabel gates the OK button until the box is ticked', () => {
    const ok = vi.fn();
    showConfirm('wipe', ok, null, { checkLabel: 'I have a backup', danger: true }, doc);
    expect(els['confirm-check-row'].hidden).toBe(false);
    expect(els['confirm-check-label'].textContent).toBe('I have a backup');
    expect(els['confirm-ok'].disabled).toBe(true);
    els['confirm-ok'].onclick();
    expect(ok).not.toHaveBeenCalled();
    els['confirm-check'].checked = true;
    els['confirm-check'].onchange();
    expect(els['confirm-ok'].disabled).toBe(false);
    els['confirm-ok'].onclick();
    expect(ok).toHaveBeenCalledTimes(1);
  });

  it('the checkbox resets and hides for the next plain confirm', () => {
    showConfirm('wipe', () => {}, null, { checkLabel: 'I have a backup' }, doc);
    els['confirm-check'].checked = true;
    showConfirm('plain', () => {}, null, {}, doc);
    expect(els['confirm-check-row'].hidden).toBe(true);
    expect(els['confirm-check'].checked).toBe(false);
    expect(els['confirm-ok'].disabled).toBe(false);
  });
});

// ---- overlay / close-button dismissal ----
function fakeDialog({ cancelVisible = true } = {}) {
  const attrs = { role: 'dialog', 'data-dismissable': '' };
  const cancel = { classList: { contains: () => false }, visible: cancelVisible, click: vi.fn() };
  const xBtn = { classList: { contains: (c) => c === 'modal__close' }, visible: true, click: vi.fn() };
  const panel = { hasAttribute: () => false, getAttribute: () => null };
  const dialog = {
    style: { display: 'flex' },
    hasAttribute: (k) => k in attrs,
    getAttribute: (k) => attrs[k] ?? null,
    querySelectorAll: () => [xBtn, cancel],
  };
  xBtn.closest = (sel) => (sel === '.modal__close' ? xBtn : sel === '[role="dialog"]' ? dialog : null);
  return { dialog, cancel, xBtn, panel };
}
function fakeDoc() {
  const ls = {};
  return {
    addEventListener: (ev, fn) => { (ls[ev] ||= []).push(fn); },
    fire: (ev, e) => (ls[ev] || []).forEach((fn) => fn(e)),
  };
}
const shown = (b) => b.visible !== false;

describe('cancelDialog', () => {
  it('clicks the footer cancel control, not the header close button', () => {
    const { dialog, cancel, xBtn } = fakeDialog();
    cancelDialog(dialog, shown);
    expect(cancel.click).toHaveBeenCalledTimes(1);
    expect(xBtn.click).not.toHaveBeenCalled();
  });
  it('hides the dialog when it has no visible cancel control', () => {
    const { dialog, cancel } = fakeDialog({ cancelVisible: false });
    cancelDialog(dialog, shown);
    expect(cancel.click).not.toHaveBeenCalled();
    expect(dialog.style.display).toBe('none');
  });
});

describe('installDialogDismiss', () => {
  it('a press that starts and ends on the overlay cancels the dialog', () => {
    const d = fakeDoc(); installDialogDismiss(d, shown);
    const { dialog, cancel } = fakeDialog();
    d.fire('mousedown', { target: dialog });
    d.fire('mouseup', { target: dialog });
    expect(cancel.click).toHaveBeenCalledTimes(1);
  });
  it('a drag from inside the panel that ends on the overlay does not', () => {
    const d = fakeDoc(); installDialogDismiss(d, shown);
    const { dialog, cancel, panel } = fakeDialog();
    d.fire('mousedown', { target: panel });
    d.fire('mouseup', { target: dialog });
    expect(cancel.click).not.toHaveBeenCalled();
  });
  it('a press on a dialog without data-dismissable does nothing', () => {
    const d = fakeDoc(); installDialogDismiss(d, shown);
    const { dialog, cancel } = fakeDialog();
    dialog.hasAttribute = () => false;
    d.fire('mousedown', { target: dialog });
    d.fire('mouseup', { target: dialog });
    expect(cancel.click).not.toHaveBeenCalled();
  });
  it('the header close button routes through the cancel control', () => {
    const d = fakeDoc(); installDialogDismiss(d, shown);
    const { cancel, xBtn } = fakeDialog();
    const e = { target: xBtn, preventDefault: vi.fn() };
    d.fire('click', e);
    expect(cancel.click).toHaveBeenCalledTimes(1);
    expect(e.preventDefault).toHaveBeenCalled();
  });
});

describe('fieldInvalidState', () => {
  it('is invalid only for a non-empty, unstyled message', () => {
    expect(fieldInvalidState({ textContent: '', style: {} })).toBe(false);
    expect(fieldInvalidState({ textContent: 'Enter a name', style: {} })).toBe(true);
    expect(fieldInvalidState({ textContent: 'Found Alice', style: { color: '#4ade80' } })).toBe(false);
  });
});
