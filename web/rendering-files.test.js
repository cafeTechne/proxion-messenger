// Attachments in history: stored room files are fetched (get_files) and filled
// in; files that are gone render an explicit unavailable state; the "📎 name"
// caption is not repeated next to the card.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createRendering, attachmentHtml, isFileCaption } from './rendering.js';
import { t } from './i18n.js';

function mkEl(over = {}) {
  const el = {
    _children: [],
    className: '', id: '', innerHTML: '', textContent: '', title: '', value: '',
    scrollTop: 0, scrollHeight: 1000, clientHeight: 500,
    style: {}, dataset: {},
    classList: { _s: new Set(), add(c){this._s.add(c);}, contains(c){return this._s.has(c);} },
    setAttribute() {}, addEventListener() {},
    appendChild(c){ el._children.push(c); if (c.id) els[c.id] = c; return c; },
    insertBefore(c){ el._children.unshift(c); return c; },
    querySelectorAll(){ return []; },
    querySelector(){ return null; },
    get firstElementChild(){ return el._children[0] || null; },
    nextElementSibling: null, nextSibling: null,
    ...over,
  };
  return el;
}

let els, sent, view, host;
beforeEach(() => {
  els = {};
  sent = [];
  view = { type: 'local_room', id: 'room-1' };
  host = { messageMap: {}, allMessages: [], userPresence: {} };
  global.WebSocket = { OPEN: 1 };
  global.document = {
    getElementById: (id) => (id in els ? els[id] : null),
    createElement: () => mkEl(),
  };
  global.localStorage = { getItem: () => null, setItem: () => {} };
});

function make() {
  return createRendering({
    getActiveView: () => view,
    getSocket: () => ({ readyState: 1, send: (m) => sent.push(JSON.parse(m)) }),
    getSelfWebId: () => 'did:key:zSelf',
    getSelfPubHex: () => null,
    getCurrentDisappearMs: () => 0,
    getMessageMap: () => host.messageMap,
    getAllMessages: () => host.allMessages,
    getUserPresence: () => host.userPresence,
    renderReactions: vi.fn(), openCtxMenu: vi.fn(), sendUpdateLastRead: vi.fn(),
    getRoomCode: () => 'CODE', renderWindow: 100, scrollBatch: 50,
  });
}

const PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
const flush = () => new Promise((r) => setTimeout(r, 0));

describe('attachmentHtml states', () => {
  it('shows "not available on this device" for a file without bytes, never a bare name', () => {
    const html = attachmentHtml({ filename: 'report.pdf', unavailable: true });
    expect(html).toContain('attachment-unavailable');
    expect(html).toContain('report.pdf');
    expect(html).toContain(t('file.unavailable'));
    expect(html).not.toContain('data:');
  });

  it('a history DM file entry with no local copy is unavailable too', () => {
    expect(attachmentHtml({ filename: '' })).toContain('attachment-unavailable');
  });

  it('a stored room file without bytes renders a loading state', () => {
    const html = attachmentHtml({ filename: 'pic.png', mime_type: 'image/png', size: 70, stored: true });
    expect(html).toContain('attachment-loading');
    expect(html).toContain(t('file.loading'));
  });

  it('renders an image preview once the bytes are there', () => {
    const html = attachmentHtml({ filename: 'pic.png', mime_type: 'image/png', size: 70, data_b64: PNG_B64 });
    expect(html).toContain('msg-image-preview');
    expect(html).toContain(`data:image/png;base64,${PNG_B64}`);
  });

  it('escapes the filename', () => {
    const html = attachmentHtml({ filename: '<img src=x onerror=alert(1)>.png', unavailable: true });
    expect(html).not.toContain('<img src=x');
  });
});

describe('isFileCaption', () => {
  it('matches the gateway caption of a file message only', () => {
    expect(isFileCaption({ content: '📎 a.png', file: {} })).toBe(true);
    expect(isFileCaption({ content: '📎', file: {} })).toBe(true);
    expect(isFileCaption({ content: '📎 a.png' })).toBe(false);
    expect(isFileCaption({ content: 'look at this', file: {} })).toBe(false);
  });
});

describe('history files in the feed', () => {
  it('does not repeat the filename as message text', () => {
    els['message-feed'] = mkEl();
    const r = make();
    r.renderMessage({
      message_id: 'file-1', thread_id: 'room-1', from_webid: 'did:key:zA', timestamp: '2026-01-01T00:00:00Z',
      content: '📎 notes.txt',
      file: { filename: 'notes.txt', mime_type: 'text/plain', size: 5, data_b64: 'aGVsbG8=' },
    });
    const row = els['msg-file-1'];
    const body = row._children[1].innerHTML;
    expect(body).not.toContain('📎 notes.txt');
    expect(body).toContain('<span class="msg-text" dir="auto"></span>');
  });

  it('asks the gateway for stored room files in one batch and fills them in', async () => {
    els['message-feed'] = mkEl();
    const r = make();
    for (const id of ['file-a', 'file-b']) {
      r.renderMessage({
        message_id: id, thread_id: 'room-1', from_webid: 'did:key:zA', timestamp: '2026-01-01T00:00:00Z',
        content: '📎 pic.png', file: { filename: 'pic.png', mime_type: 'image/png', size: 70, stored: true },
      });
    }
    await flush();
    expect(sent).toEqual([{ cmd: 'get_files', message_ids: ['file-a', 'file-b'] }]);

    // Answer: one file arrives, the other is gone.
    const card = { outerHTML: '' };
    els['msg-file-a'].querySelector = (sel) => (sel === '.attachment' ? card : null);
    r.fillAttachment('file-a', { filename: 'pic.png', mime_type: 'image/png', size: 70, data_b64: PNG_B64 });
    expect(host.messageMap['file-a'].file.data_b64).toBe(PNG_B64);
    expect(card.outerHTML).toContain('msg-image-preview');

    const card2 = { outerHTML: '' };
    els['msg-file-b'].querySelector = (sel) => (sel === '.attachment' ? card2 : null);
    r.fillAttachment('file-b', null);
    expect(host.messageMap['file-b'].file.unavailable).toBe(true);
    expect(card2.outerHTML).toContain(t('file.unavailable'));
  });

  it('does not ask for files that already have bytes or are unavailable', async () => {
    els['message-feed'] = mkEl();
    const r = make();
    r.renderMessage({
      message_id: 'file-c', thread_id: 'room-1', from_webid: 'did:key:zA', timestamp: '2026-01-01T00:00:00Z',
      content: '📎 old.pdf', file: { filename: 'old.pdf', unavailable: true },
    });
    await flush();
    expect(sent).toEqual([]);
  });
});
