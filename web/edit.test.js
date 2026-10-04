import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createEdit } from './edit.js';

let els;
function mkEl(over = {}) {
  return {
    style: {}, value: '', innerText: '', innerHTML: '', className: '', type: '',
    onclick: null, onkeydown: null,
    querySelector: () => null, closest: () => null,
    replaceWith() {}, appendChild() {}, remove() {}, after() {}, focus() {}, setAttribute() {},
    ...over,
  };
}
beforeEach(() => {
  els = {};
  global.document = {
    getElementById: (id) => (id in els ? els[id] : null),
    createElement: () => mkEl(),
  };
});

function make(over = {}) {
  const sent = [];
  const socket = { readyState: 1, send: (s) => sent.push(JSON.parse(s)) };
  const messageMap = over.messageMap ?? {};
  const edit = createEdit({
    getSocket: () => (over.socket === undefined ? socket : over.socket),
    getActiveView: () => (over.activeView === undefined ? { type: 'local_room', id: 'room-1' } : over.activeView),
    getClientDid: () => over.clientDid ?? 'did:key:zSelf',
    getMessageMap: () => messageMap,
  });
  return { edit, sent, messageMap };
}

describe('startEdit', () => {
  it('records the editing id and swaps the text for an input', () => {
    const replaceWith = vi.fn();
    const textEl = mkEl({ innerText: 'hi', replaceWith });
    els['msg-m1'] = mkEl({ querySelector: (sel) => (sel === '.msg-text' ? textEl : null) });
    const { edit } = make();
    edit.startEdit('m1');
    expect(edit.state.editingMsgId).toBe('m1');
    expect(replaceWith).toHaveBeenCalled();
  });
  it('is a no-op when the message element is missing', () => {
    const { edit } = make();
    edit.startEdit('nope');
    expect(edit.state.editingMsgId).toBe(null);
  });
});

describe('commitEdit', () => {
  it('sends edit_local_message for a local room and clears editing state', () => {
    els['msg-m1'] = mkEl();
    const { edit, sent } = make({ activeView: { type: 'local_room', id: 'room-1' } });
    edit.state.editingMsgId = 'm1';
    edit.commitEdit('m1', '  new text  ');
    expect(sent).toContainEqual({
      cmd: 'edit_local_message', message_id: 'm1', thread_id: 'room-1',
      content: 'new text', from_webid: 'did:key:zSelf',
    });
    expect(edit.state.editingMsgId).toBe(null);
  });
  it('sends edit_message with cert_id for a DM', () => {
    els['msg-m1'] = mkEl();
    const { edit, sent } = make({ activeView: { type: 'dm', id: 'cert-9' } });
    edit.commitEdit('m1', 'x');
    expect(sent).toContainEqual({ cmd: 'edit_message', message_id: 'm1', content: 'x', cert_id: 'cert-9' });
  });
  it('refuses empty content', () => {
    const { edit, sent } = make();
    edit.commitEdit('m1', '   ');
    expect(sent).toHaveLength(0);
  });
});

describe('handleMessageEdited', () => {
  it('updates the cached message content', () => {
    const textEl = mkEl();
    els['msg-m1'] = mkEl({ querySelector: (sel) => (sel === '.msg-text' ? textEl : null) });
    const { edit, messageMap } = make({ messageMap: { m1: { content: 'old' } } });
    edit.handleMessageEdited({ message_id: 'm1', new_content: 'fresh', edited_at: '2026-01-01T00:00:00Z' });
    expect(messageMap.m1.content).toBe('fresh');
    expect(textEl.innerText).toBe('fresh');
  });
});

describe('multi-line inline editor', () => {
  function open() {
    const created = [];
    global.document.createElement = (tag) => { const el = mkEl({ tag }); created.push(el); return el; };
    const textEl = mkEl({ innerText: 'line one' });
    let current = textEl;
    textEl.replaceWith = (n) => { current = n; };
    const msgEl = mkEl({
      querySelector: (sel) => {
        if (sel === '.msg-text') return current === textEl ? textEl : null;
        if (sel === '.edit-input') return current !== textEl && current.tag === 'textarea' ? current : null;
        return null;
      },
    });
    els['msg-m1'] = msgEl;
    const h = make();
    h.edit.startEdit('m1');
    const ta = created.find(e => e.tag === 'textarea');
    const restore = (n) => { current = n; };
    ta.replaceWith = restore;
    return { ...h, ta, textEl, current: () => current };
  }
  const key = (over) => ({ key: 'Enter', shiftKey: false, isComposing: false, preventDefault: vi.fn(), stopPropagation: vi.fn(), ...over });

  it('uses an auto-growing textarea with the edit-input class', () => {
    const { ta } = open();
    expect(ta.tag).toBe('textarea');
    expect(ta.className).toBe('edit-input');
    expect(ta.value).toBe('line one');
    expect(typeof ta.oninput).toBe('function');
  });
  it('Enter saves, Shift+Enter leaves the newline to the textarea', () => {
    const { ta, sent } = open();
    const shift = key({ shiftKey: true });
    ta.onkeydown(shift);
    expect(shift.preventDefault).not.toHaveBeenCalled();
    expect(sent).toHaveLength(0);
    ta.value = 'line one\nline two';
    ta.onkeydown(key());
    expect(sent[0]).toMatchObject({ cmd: 'edit_local_message', content: 'line one\nline two' });
  });
  it('ignores Enter while an IME composition is active', () => {
    const { ta, sent } = open();
    ta.onkeydown(key({ isComposing: true }));
    ta.onkeydown(key({ keyCode: 229 }));
    expect(sent).toHaveLength(0);
  });
  it('Escape cancels, restores the original element and stops the global handler', () => {
    const { ta, edit, textEl, current } = open();
    const esc = key({ key: 'Escape' });
    ta.onkeydown(esc);
    expect(esc.stopPropagation).toHaveBeenCalled();
    expect(edit.state.editingMsgId).toBe(null);
    expect(current()).toBe(textEl);
  });
  it('cancelEdit from outside (global Escape) still restores the text', () => {
    const { edit, textEl, current } = open();
    edit.cancelEdit('m1', 'line one');
    expect(current()).toBe(textEl);
    expect(edit.state.editingMsgId).toBe(null);
  });
});
