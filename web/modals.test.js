import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createModals } from './modals.js';

let els;
function mkEl(over = {}) {
  const children = [];
  return {
    style: {}, innerHTML: '', textContent: '', className: '', id: '',
    dataset: {}, _children: children,
    appendChild: (c) => children.push(c),
    addEventListener() {}, remove() {},
    querySelector: () => mkEl(), querySelectorAll: () => [],
    ...over,
  };
}
beforeEach(() => {
  els = {};
  global.document = {
    getElementById: (id) => (els[id] ||= mkEl()),
    createElement: () => mkEl(),
    querySelectorAll: () => [],
    body: { appendChild() {} },
  };
});

function make(over = {}) {
  const sent = [];
  const sendCmdCalls = [];
  const socket = { readyState: 1, send: (s) => sent.push(JSON.parse(s)) };
  const modals = createModals({
    getSocket: () => (over.socket === undefined ? socket : over.socket),
    getActiveView: () => (over.activeView === undefined ? { type: 'local_room', id: 'room-1' } : over.activeView),
    sendCmd: (cmd, payload) => sendCmdCalls.push({ cmd, payload }),
    showToast: () => {},
    renderMessage: over.renderMessage ?? (() => {}),
  });
  return { modals, sent, sendCmdCalls };
}

describe('openForwardModal', () => {
  it('records the message id and opens the modal', () => {
    const { modals } = make();
    modals.openForwardModal('m1');
    expect(modals.state.forwardingMsgId).toBe('m1');
    expect(els['forward-modal'].style.display).toBe('flex');
  });
  it('shows an empty-state when there are no rooms', () => {
    const { modals } = make();
    modals.openForwardModal('m1');
    expect(els['forward-thread-list'].innerHTML).toContain('modal.noRoomsToForward');
  });
});

describe('openSchedulePicker', () => {
  it('toggles the picker open then closed', () => {
    const { modals } = make();
    els['schedule-picker'] = mkEl({ style: { display: 'none' } });
    modals.openSchedulePicker();
    expect(els['schedule-picker'].style.display).toBe('flex');
    modals.openSchedulePicker();
    expect(els['schedule-picker'].style.display).toBe('none');
  });
});

describe('openIntegrationsPanel', () => {
  it('requests the webhook list for the active thread', () => {
    const { modals, sent } = make({ activeView: { type: 'local_room', id: 'room-1' } });
    modals.openIntegrationsPanel();
    expect(sent).toContainEqual({ cmd: 'list_webhooks', thread_id: 'room-1' });
  });
  it('is a no-op without an active view', () => {
    const { modals, sent } = make({ activeView: null });
    modals.openIntegrationsPanel();
    expect(sent).toHaveLength(0);
  });
});

describe('renderSearchResults', () => {
  it('renders a message per result', () => {
    const rendered = [];
    const { modals } = make({ renderMessage: (m) => rendered.push(m) });
    modals.renderSearchResults({ query: 'hi', results: [{ message_id: 'm1' }, { message_id: 'm2' }] });
    expect(rendered).toHaveLength(2);
    expect(rendered[0].is_search_result).toBe(true);
  });
  it('shows a no-matches notice for empty results', () => {
    const { modals } = make();
    modals.renderSearchResults({ query: 'zzz', results: [] });
    expect(els['message-feed'].innerHTML).toContain('No matches found');
  });
});
