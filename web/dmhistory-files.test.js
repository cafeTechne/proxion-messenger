// DM files are kept in this device's history store (the gateway has no copy),
// purged with the message, and handed to a paired device without the bytes.
import 'fake-indexeddb/auto';
import { describe, it, expect, beforeEach, vi } from 'vitest';

const _h = vi.hoisted(() => ({ acct: null }));
vi.mock('./auth.js', () => ({
  accountDbName: (base) => (_h.acct ? `${base}::${_h.acct}` : base),
}));

import {
  dmHistorySave, dmHistoryLoad, dmHistoryClearAll, dmHistorySetEnabled,
  dmHistoryDelete, dmHistoryDeleteBefore, dmHistoryExportRecent,
  applyLocalRecord, fileForRecord, MAX_FILE_B64,
} from './dmhistory.js';

const FILE = { filename: 'pic.png', mime_type: 'image/png', size: 4, data_b64: 'AAAAAA==', width: 2, height: 2 };
const msg = (id, ts, extra = {}) => ({
  message_id: id, thread_id: 't1', from_webid: 'did:key:zP', content: '📎 pic.png',
  timestamp: ts, file: FILE, ...extra,
});

beforeEach(async () => {
  dmHistorySetEnabled(true);
  _h.acct = null;
  await dmHistoryClearAll();
});

describe('DM file history', () => {
  it('keeps the file bytes so the file renders after a reload', async () => {
    await dmHistorySave(msg('file-1', '2026-01-01T00:00:00Z'));
    const [rec] = await dmHistoryLoad('t1');
    expect(rec.file).toEqual({ filename: 'pic.png', mime_type: 'image/png', size: 4, data_b64: 'AAAAAA==', width: 2, height: 2 });
  });

  it('the cached file replaces the gateway history entry (which has no bytes)', async () => {
    await dmHistorySave(msg('file-1', '2026-01-01T00:00:00Z'));
    const [rec] = await dmHistoryLoad('t1');
    const fromGateway = { message_id: 'file-1', content: '📎', file: { filename: '', unavailable: true } };
    expect(applyLocalRecord(fromGateway, rec)).toBe(true);
    expect(fromGateway.file.data_b64).toBe('AAAAAA==');
    expect(fromGateway.content).toBe('📎 pic.png');
  });

  it('delete and disappearing expiry purge the file with the message', async () => {
    await dmHistorySave(msg('file-1', '2026-01-01T00:00:00Z'));
    await dmHistorySave(msg('file-2', '2026-01-03T00:00:00Z'));
    await dmHistoryDelete('file-2');
    await dmHistoryDeleteBefore('t1', '2026-01-02T00:00:00Z');
    expect(await dmHistoryLoad('t1')).toEqual([]);
  });

  it('a too-large file keeps only its metadata and is marked unavailable', () => {
    const big = fileForRecord({ ...FILE, data_b64: 'A'.repeat(MAX_FILE_B64 + 4) });
    expect(big.data_b64).toBeUndefined();
    expect(big.unavailable).toBe(true);
  });

  it('the pairing hand-off carries file names, not bytes', async () => {
    await dmHistorySave(msg('file-1', '2026-01-01T00:00:00Z'));
    const [rec] = await dmHistoryExportRecent();
    expect(rec.file.data_b64).toBeUndefined();
    expect(rec.file.unavailable).toBe(true);
    expect(rec.file.filename).toBe('pic.png');
    // The local copy itself is untouched.
    const [local] = await dmHistoryLoad('t1');
    expect(local.file.data_b64).toBe('AAAAAA==');
  });
});
