import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createEdit } from './edit.js';

let els;
function mkEl(over = {}) {
  return {
    style: {}, value: '', innerText: '', innerHTML: '', className: '', type: '',
    onclick: null, onkeydown: null,
    querySelector: () => null, closest: () => null,
    replaceWith() {}, appendChild() {}, remove() {}, after() {}, focus() {},
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
