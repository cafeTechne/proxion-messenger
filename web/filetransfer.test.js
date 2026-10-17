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
  const toasts = [];
  const ft = createFileTransfer({
    sendCmd: (cmd, payload) => { calls.push({ cmd, payload }); overrides.onCmd?.(cmd, payload); },
    showToast: (m) => toasts.push(m),
    renderMessage: overrides.renderMessage || (() => {}),
    getActiveView: overrides.getActiveView || (() => null),
    resolveSender: overrides.resolveSender,
  });
  return { ft, calls, toasts };
}

const tick = (ms = 5) => new Promise(r => setTimeout(r, ms));
const fileOf = (n) => ({ name: 'big.pdf', type: 'application/pdf', arrayBuffer: async () => new Uint8Array(n).buffer });
async function startSend(ft, calls, n) {
  const p = ft.sendFileChunked(fileOf(n), 'did:bob');
  await tick();
  const offer = calls.find(c => c.cmd === 'file_offer');
  ft.handleFileAccept({ file_id: offer.payload.file_id });
  await tick();
  return { p, id: offer.payload.file_id, total: offer.payload.total_chunks };
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
  it('rejects a nonsensical total_chunks (bad/huge offer)', () => {
    for (const tc of [0, -1, 1e9, 2.5, undefined]) {
      const { ft, calls } = makeFT();
      ft.handleFileOffer({ file_id: 'fb', from_webid: 'did:x', filename: 'a', mime_type: 'x', size_bytes: 1000, total_chunks: tc });
      expect(calls[0].cmd).toBe('file_reject');
      expect(calls[0].payload.reason).toBe('bad_offer');
    }
  });
});

describe('handleFileComplete integrity', () => {
  it('does NOT render a corrupt file when a chunk is missing', () => {
    let rendered = null;
    const { ft } = makeFT({ renderMessage: (m) => { rendered = m; } });
    ft.handleFileOffer({ file_id: 'fm', from_webid: 'did:x', filename: 'a.bin', mime_type: 'x', size_bytes: 5, total_chunks: 3 });
    // Only 2 of 3 chunks arrive (middle one dropped).
    ft.handleFileChunk({ file_id: 'fm', seq: 0, data: 'AA==' });
    ft.handleFileChunk({ file_id: 'fm', seq: 2, data: 'AA==' });
    ft.handleFileComplete({ file_id: 'fm' });
    expect(rendered).toBeNull(); // no silently-corrupt file rendered
  });

  it('does NOT let out-of-range seqs satisfy the completeness check', () => {
    // A malicious sender delivers `total` chunks but with seqs outside [0,total).
    // Without seq validation these bump `received` to total while the real
    // indices stay empty, so file_complete would pass and render an empty file.
    let rendered = null;
    const { ft } = makeFT({ renderMessage: (m) => { rendered = m; } });
    ft.handleFileOffer({ file_id: 'fx', from_webid: 'did:x', filename: 'a.bin', mime_type: 'x', size_bytes: 3, total_chunks: 3 });
    for (const seq of [10, 11, 12]) ft.handleFileChunk({ file_id: 'fx', seq, data: 'AA==' });
    ft.handleFileComplete({ file_id: 'fx' });
    expect(rendered).toBeNull();
  });

  it('ignores non-integer and negative seqs', () => {
    let rendered = null;
    const { ft } = makeFT({ renderMessage: (m) => { rendered = m; } });
    ft.handleFileOffer({ file_id: 'fy', from_webid: 'did:x', filename: 'a.bin', mime_type: 'x', size_bytes: 2, total_chunks: 2 });
    ft.handleFileChunk({ file_id: 'fy', seq: '0', data: 'AA==' });   // string, not index 0
    ft.handleFileChunk({ file_id: 'fy', seq: -1, data: 'AA==' });
    ft.handleFileChunk({ file_id: 'fy', seq: 1.5, data: 'AA==' });
    ft.handleFileComplete({ file_id: 'fy' });
    expect(rendered).toBeNull();
  });

  it('ignores a chunk whose data is not a string', () => {
    let rendered = null;
    const { ft } = makeFT({ renderMessage: (m) => { rendered = m; } });
    ft.handleFileOffer({ file_id: 'fz', from_webid: 'did:x', filename: 'a.bin', mime_type: 'x', size_bytes: 1, total_chunks: 1 });
    ft.handleFileChunk({ file_id: 'fz', seq: 0, data: { evil: true } });
    ft.handleFileComplete({ file_id: 'fz' });
    expect(rendered).toBeNull();
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

describe('receive-side resource bounds', () => {
  function offer(ft, id, chunks = 1) {
    ft.handleFileOffer({ file_id: id, from_webid: 'did:x', filename: id, mime_type: 'x', size_bytes: 10, total_chunks: chunks });
  }

  it('caps concurrent incoming transfers and rejects the overflow', () => {
    const { ft, calls } = makeFT();
    for (let i = 0; i < ft.MAX_CONCURRENT_INCOMING; i++) offer(ft, 'f' + i);
    expect(ft._incomingCount()).toBe(ft.MAX_CONCURRENT_INCOMING);
    calls.length = 0;
    offer(ft, 'overflow');
    expect(calls[0].cmd).toBe('file_reject');
    expect(calls[0].payload.reason).toBe('busy');
    expect(ft._incomingCount()).toBe(ft.MAX_CONCURRENT_INCOMING); // not stored
  });

  it('a completed transfer frees its slot', () => {
    const { ft } = makeFT();
    offer(ft, 'done', 1);
    ft.handleFileChunk({ file_id: 'done', seq: 0, data: btoa('z') });
    ft.handleFileComplete({ file_id: 'done' });
    expect(ft._incomingCount()).toBe(0);
  });

  it('reclaims an abandoned transfer after the idle timeout', () => {
    vi.useFakeTimers();
    try {
      const { ft } = makeFT();
      offer(ft, 'stall', 3);
      ft.handleFileChunk({ file_id: 'stall', seq: 0, data: btoa('a') });
      expect(ft._incomingCount()).toBe(1);
      vi.advanceTimersByTime(ft.TRANSFER_IDLE_TIMEOUT_MS + 1);
      expect(ft._incomingCount()).toBe(0); // buffered chunks freed
    } finally {
      vi.useRealTimers();
    }
  });

  it('a chunk resets the idle timer (active transfer is not reclaimed)', () => {
    vi.useFakeTimers();
    try {
      const { ft } = makeFT();
      offer(ft, 'active', 3);
      const half = Math.floor(ft.TRANSFER_IDLE_TIMEOUT_MS * 0.75);
      ft.handleFileChunk({ file_id: 'active', seq: 0, data: btoa('a') });
      vi.advanceTimersByTime(half);
      ft.handleFileChunk({ file_id: 'active', seq: 1, data: btoa('b') });  // activity
      vi.advanceTimersByTime(half);                                        // past original deadline
      expect(ft._incomingCount()).toBe(1);                                 // still alive
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('accept/reject settle the send handshake', () => {
  it('handleFileAccept resolves so chunks can flow', async () => {
    // Stand in for the gateway (chunk acks) and the receiver (final receipt).
    let ft;
    const made = makeFT({ onCmd: (cmd, p) => {
      if (cmd === 'file_chunk') queueMicrotask(() => ft.handleFileChunkAck({ file_id: p.file_id, seq: p.seq }));
      if (cmd === 'file_complete') queueMicrotask(() => ft.handleFileReceived({ file_id: p.file_id }));
    } });
    ft = made.ft;
    const { p } = await startSend(ft, made.calls, 3);
    expect(await p).toBe(true);
    expect(made.calls.some(c => c.cmd === 'file_chunk')).toBe(true);
    expect(made.calls.some(c => c.cmd === 'file_complete')).toBe(true);
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

describe('send side delivery (paced, confirmed)', () => {
  const CHUNK = 64 * 1024;
  const SENT = /file\.sent|^Sent /;
  const FAILED = /deliveryFailed|did not reach/;
  it('keeps at most 8 chunks unacked and completes only after every ack', async () => {
    const { ft, calls, toasts } = makeFT();
    const { p, id, total } = await startSend(ft, calls, 20 * CHUNK);
    expect(total).toBe(20);
    const chunks = () => calls.filter(c => c.cmd === 'file_chunk');
    expect(chunks()).toHaveLength(8);
    for (let seq = 0; seq < 19; seq++) ft.handleFileChunkAck({ file_id: id, seq });
    await tick();
    expect(chunks()).toHaveLength(20);
    expect(calls.some(c => c.cmd === 'file_complete')).toBe(false);   // seq 19 not acked
    ft.handleFileChunkAck({ file_id: id, seq: 19 });
    await tick();
    expect(calls.some(c => c.cmd === 'file_complete')).toBe(true);
    // Every chunk went out, but nothing says "Sent" until the receiver confirms.
    expect(toasts.some(m => SENT.test(m))).toBe(false);
    ft.handleFileReceived({ file_id: id });
    expect(await p).toBe(true);
    expect(toasts.some(m => SENT.test(m))).toBe(true);
  });

  it('a relay failure stops the transfer and reports it instead of "Sent"', async () => {
    const { ft, calls, toasts } = makeFT();
    const { p, id } = await startSend(ft, calls, 20 * CHUNK);
    ft.handleFileChunkAck({ file_id: id, seq: 0 });
    ft.handleFileSendFailed({ file_id: id, reason: 'relay_failed' });
    expect(await p).toBe(false);
    expect(calls.filter(c => c.cmd === 'file_chunk').length).toBeLessThan(20);
    expect(calls.some(c => c.cmd === 'file_complete')).toBe(false);
    expect(toasts.some(m => SENT.test(m))).toBe(false);
    expect(toasts.some(m => FAILED.test(m))).toBe(true);
  });

  it('a receiver that could not assemble the file fails the send', async () => {
    const { ft, calls, toasts } = makeFT();
    const { p, id } = await startSend(ft, calls, 2 * CHUNK);
    ft.handleFileChunkAck({ file_id: id, seq: 0 });
    ft.handleFileChunkAck({ file_id: id, seq: 1 });
    await tick();
    expect(calls.some(c => c.cmd === 'file_complete')).toBe(true);
    ft.handleFileReject({ file_id: id, reason: 'incomplete' });
    expect(await p).toBe(false);
    expect(toasts.some(m => FAILED.test(m))).toBe(true);
    expect(toasts.some(m => /declined/i.test(m))).toBe(false);
  });
});

describe('receive side confirms and names the sender', () => {
  it('confirms a complete file, and reports an incomplete one back', () => {
    const { ft, calls } = makeFT();
    ft.handleFileOffer({ file_id: 'ok', from_webid: 'did:key:z6MkGw', filename: 'a.txt', mime_type: 'text/plain', size_bytes: 3, total_chunks: 1 });
    ft.handleFileChunk({ file_id: 'ok', seq: 0, data: btoa('abc') });
    ft.handleFileComplete({ file_id: 'ok' });
    expect(calls.find(c => c.cmd === 'file_received')?.payload).toEqual({ to_webid: 'did:key:z6MkGw', file_id: 'ok' });

    ft.handleFileOffer({ file_id: 'bad', from_webid: 'did:key:z6MkGw', filename: 'a.txt', mime_type: 'text/plain', size_bytes: 6, total_chunks: 2 });
    ft.handleFileChunk({ file_id: 'bad', seq: 0, data: btoa('abc') });
    ft.handleFileComplete({ file_id: 'bad' });
    const rej = calls.find(c => c.cmd === 'file_reject' && c.payload.file_id === 'bad');
    expect(rej.payload.reason).toBe('incomplete');
  });

  it('labels the file with the contact, not a slice of the gateway did', () => {
    const rendered = [];
    const gwDid = 'did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK';
    const { ft } = makeFT({
      renderMessage: (m) => rendered.push(m),
      resolveSender: (w) => (w === gwDid ? { name: 'Bob', threadId: 'cert-bob' } : null),
    });
    ft.handleFileOffer({ file_id: 'n1', from_webid: gwDid, filename: 'a.txt', mime_type: 'text/plain', size_bytes: 1, total_chunks: 1 });
    ft.handleFileChunk({ file_id: 'n1', seq: 0, data: btoa('a') });
    ft.handleFileComplete({ file_id: 'n1' });
    expect(rendered[0].from_display_name).toBe('Bob');
    expect(rendered[0].thread_id).toBe('cert-bob');

    // Unknown sender: the distinguishing tail, never "did:key:z6Mk".
    const { ft: ft2 } = makeFT({ renderMessage: (m) => rendered.push(m) });
    ft2.handleFileOffer({ file_id: 'n2', from_webid: gwDid, filename: 'a.txt', mime_type: 'text/plain', size_bytes: 1, total_chunks: 1 });
    ft2.handleFileChunk({ file_id: 'n2', seq: 0, data: btoa('a') });
    ft2.handleFileComplete({ file_id: 'n2' });
    expect(rendered[1].from_display_name).not.toBe(gwDid.slice(0, 12));
    expect(rendered[1].from_display_name.endsWith(gwDid.slice(-10))).toBe(true);
  });
});
