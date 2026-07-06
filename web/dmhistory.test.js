// dmhistory.js — local DM plaintext cache: retention cap, enable switch, clear.
import 'fake-indexeddb/auto';
import { describe, it, expect, beforeEach } from 'vitest';
import {
  dmHistorySave, dmHistoryLoad, dmHistoryClearAll,
  dmHistorySetEnabled, dmHistoryEnabled, planEviction,
} from './dmhistory.js';

beforeEach(async () => {
  dmHistorySetEnabled(true);
  await dmHistoryClearAll();
});

describe('planEviction (pure)', () => {
  it('returns [] when under the cap', () => {
    const rows = [{ message_id: 'a', timestamp: '1' }, { message_id: 'b', timestamp: '2' }];
    expect(planEviction(rows, 5)).toEqual([]);
  });
  it('evicts the OLDEST beyond the cap, keeping the newest N', () => {
    const rows = [
      { message_id: 'm1', timestamp: '2024-01-01' },
      { message_id: 'm3', timestamp: '2024-01-03' },
      { message_id: 'm2', timestamp: '2024-01-02' },
      { message_id: 'm4', timestamp: '2024-01-04' },
    ];
    expect(planEviction(rows, 2).sort()).toEqual(['m1', 'm2']);
  });
  it('handles non-array input', () => {
    expect(planEviction(null, 2)).toEqual([]);
  });
});

describe('dmHistorySave enable switch', () => {
  it('no-ops when disabled', async () => {
    dmHistorySetEnabled(false);
    expect(dmHistoryEnabled()).toBe(false);
    await dmHistorySave({ message_id: 'x', thread_id: 't', content: 'secret' });
    expect(await dmHistoryLoad('t')).toHaveLength(0);
  });
  it('persists when enabled', async () => {
    await dmHistorySave({ message_id: 'x', thread_id: 't', content: 'hi' });
    const rows = await dmHistoryLoad('t');
    expect(rows).toHaveLength(1);
    expect(rows[0].content).toBe('hi');
  });
});

describe('per-thread retention cap', () => {
  it('never keeps more than MAX_PER_THREAD, evicting oldest first', async () => {
    // MAX_PER_THREAD is 2000; write 2003 and assert the 3 oldest are gone.
    for (let i = 0; i < 2003; i++) {
      await dmHistorySave({
        message_id: `m${String(i).padStart(5, '0')}`,
        thread_id: 'cap',
        content: `msg ${i}`,
        timestamp: new Date(1700000000000 + i * 1000).toISOString(),
      });
    }
    const rows = await dmHistoryLoad('cap', 0);
    expect(rows.length).toBe(2000);
    // Oldest three (m00000..m00002) evicted; newest retained.
    const ids = new Set(rows.map((r) => r.message_id));
    expect(ids.has('m00000')).toBe(false);
    expect(ids.has('m00002')).toBe(false);
    expect(ids.has('m02002')).toBe(true);
  }, 60000);
});

describe('dmHistoryClearAll', () => {
  it('wipes everything', async () => {
    await dmHistorySave({ message_id: 'a', thread_id: 't1', content: '1' });
    await dmHistorySave({ message_id: 'b', thread_id: 't2', content: '2' });
    await dmHistoryClearAll();
    expect(await dmHistoryLoad('t1')).toHaveLength(0);
    expect(await dmHistoryLoad('t2')).toHaveLength(0);
  });
});
