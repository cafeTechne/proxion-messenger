import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createFileTransfer } from './filetransfer.js';

// Minimal DOM stub so the progress-indicator helpers don't throw in node env.
beforeEach(() => {
  const els = {};
  global.document = {
    getElementById: (id) => els[id] || null,
    createElement: () => {
      const el = { id: '', style: {}, textContent: '', remove() { delete els[this.id]; } };
      return el;
    },
    body: { appendChild: (el) => { els[el.id] = el; } },
  };
});

function makeFT(overrides = {}) {
  const calls = [];
  const ft = createFileTransfer({
    sendCmd: (cmd, payload) => calls.push({ cmd, payload }),
    showToast: () => {},
    renderMessage: overrides.renderMessage || (() => {}),
    getActiveView: overrides.getActiveView || (() => null),
  });
  return { ft, calls };
}

describe('handleFileOffer', () => {
  it('rejects files over the size ceiling', () => {
    const { ft, calls } = makeFT();
    ft.handleFileOffer({ file_id: 'f1', from_webid: 'did:x', size_bytes: ft.MAX_FILE_BYTES + 1, total_chunks: 1 });
    expect(calls).toHaveLength(1);
    expect(calls[0].cmd).toBe('file_reject');
    expect(calls[0].payload.reason).toBe('too_large');
  });
  it('accepts files within the ceiling', () => {
    const { ft, calls } = makeFT();
    ft.handleFileOffer({ file_id: 'f2', from_webid: 'did:x', filename: 'a.png', mime_type: 'image/png', size_bytes: 1000, total_chunks: 1 });
    expect(calls[0].cmd).toBe('file_accept');
    expect(calls[0].payload.file_id).toBe('f2');
  });
});

describe('handleFileChunk + handleFileComplete reassembly', () => {
  it('reassembles chunks into a rendered message with correct bytes', () => {
    const rendered = [];
    const { ft } = makeFT({ renderMessage: (m) => rendered.push(m), getActiveView: () => null });
    // Offer establishes the incoming record (2 chunks)
    ft.handleFileOffer({ file_id: 'f3', from_webid: 'did:bob', filename: 'hi.txt', mime_type: 'text/plain', size_bytes: 6, total_chunks: 2 });
    // "abc" + "def" base64
    const b64 = (s) => btoa(s);
    ft.handleFileChunk({ file_id: 'f3', seq: 0, data: b64('abc') });
    ft.handleFileChunk({ file_id: 'f3', seq: 1, data: b64('def') });
    ft.handleFileComplete({ file_id: 'f3' });
    expect(rendered).toHaveLength(1);
    const msg = rendered[0];
    expect(msg.file.filename).toBe('hi.txt');
    // decode the reassembled data_b64 → "abcdef"
    expect(atob(msg.file.data_b64)).toBe('abcdef');
    expect(msg.from_webid).toBe('did:bob');
  });
  it('routes the rendered message to the active DM thread when it matches', () => {
    const rendered = [];
    const { ft } = makeFT({
      renderMessage: (m) => rendered.push(m),
      getActiveView: () => ({ id: 'cert-123', peerWebid: 'did:bob' }),
    });
    ft.handleFileOffer({ file_id: 'f4', from_webid: 'did:bob', filename: 'x', mime_type: 'text/plain', size_bytes: 1, total_chunks: 1 });
    ft.handleFileChunk({ file_id: 'f4', seq: 0, data: btoa('z') });
    ft.handleFileComplete({ file_id: 'f4' });
    expect(rendered[0].thread_id).toBe('cert-123');
  });
});

describe('accept/reject settle the send handshake', () => {
  it('handleFileAccept resolves so chunks can flow', async () => {
    const { ft, calls } = makeFT();
    const file = { name: 'f.bin', type: 'application/octet-stream', arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer };
    const p = ft.sendFileChunked(file, 'did:bob');
    // sendFileChunked awaits arrayBuffer() before emitting the offer — let it run
    await new Promise(r => setTimeout(r, 10));
    const offer = calls.find(c => c.cmd === 'file_offer');
    ft.handleFileAccept({ file_id: offer.payload.file_id });
    await p;
    expect(calls.some(c => c.cmd === 'file_chunk')).toBe(true);
    expect(calls.some(c => c.cmd === 'file_complete')).toBe(true);
  });
  it('handleFileReject aborts the send (no chunks sent)', async () => {
    const { ft, calls } = makeFT();
    const file = { name: 'f.bin', type: '', arrayBuffer: async () => new Uint8Array([1]).buffer };
    const p = ft.sendFileChunked(file, 'did:bob');
    await new Promise(r => setTimeout(r, 10));
    const offer = calls.find(c => c.cmd === 'file_offer');
    ft.handleFileReject({ file_id: offer.payload.file_id, reason: 'declined' });
    await p;
    expect(calls.some(c => c.cmd === 'file_chunk')).toBe(false);
  });
});
