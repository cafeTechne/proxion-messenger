// dmtext.js: no DM surface shows an E2E envelope; local plaintext search.
import 'fake-indexeddb/auto';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { readFileSync } from 'node:fs';

vi.mock('./auth.js', () => ({ accountDbName: (base) => base }));

import {
  looksLikeCiphertext, isSealedCopy, dmDisplayText, unsealCopy, unsealList,
  mergeSearchHits, previewText,
} from './dmtext.js';
import { UNDECRYPTABLE } from './e2e-secondary.js';
import {
  dmHistorySave, dmHistoryClearAll, dmHistorySetEnabled, dmHistoryGet,
  dmHistorySearch, matchesQuery,
} from './dmhistory.js';

const CT = 'q3Zx9_Lw-0aBcDeFgHiJkLmNoPqRsTuV';

describe('isSealedCopy', () => {
  it('trusts the gateway flag and the stored content type', () => {
    expect(isSealedCopy({ content: 'hi', e2e: true })).toBe(true);
    expect(isSealedCopy({ content: 'hi', content_type: 'e2e' })).toBe(true);
    expect(isSealedCopy({ content: CT, e2e: false }, true)).toBe(false);
  });
  it('recognises legacy unflagged ciphertext in DMs only', () => {
    expect(isSealedCopy({ content: CT }, true)).toBe(true);
    expect(isSealedCopy({ content: CT }, false)).toBe(false);
  });
  it('never mistakes ordinary text for ciphertext', () => {
    for (const s of ['hello there', 'supercalifragilisticexpialidocious',
      'https://example.com/a-b_c', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'short-Token1']) {
      expect(looksLikeCiphertext(s)).toBe(false);
    }
  });
});

describe('dmDisplayText / unsealCopy', () => {
  it('prefers this device plaintext, else the placeholder, never the envelope', () => {
    expect(dmDisplayText({ content: CT, e2e: true }, { plaintext: 'secret' })).toBe('secret');
    expect(dmDisplayText({ content: CT, e2e: true })).toBe(UNDECRYPTABLE);
    expect(dmDisplayText({ content: CT, e2e: true }, { plaintext: UNDECRYPTABLE })).toBe(UNDECRYPTABLE);
    expect(dmDisplayText({ content: 'room text' })).toBe('room text');
  });
  it('history-off load: a sealed copy becomes the placeholder and is no longer e2e', () => {
    const out = unsealCopy({ message_id: 'm1', content: CT, e2e: true, content_type: 'e2e' }, { isDm: true });
    expect(out).toMatchObject({ message_id: 'm1', content: UNDECRYPTABLE, e2e: false });
    expect(out.content_type).toBeUndefined();
    expect(JSON.stringify(out)).not.toContain(CT);
  });
  it('leaves an unsealed message untouched', () => {
    const m = { message_id: 'm2', content: 'hello' };
    expect(unsealCopy(m, { isDm: true })).toBe(m);
  });
});

describe('unsealList (pins)', () => {
  it('fills sealed pins from the local copy and never returns ciphertext', async () => {
    const pins = [
      { message_id: 'a', content: '', e2e: true },
      { message_id: 'b', content: CT },
      { message_id: 'c', content: 'plain pin' },
    ];
    const asked = [];
    const out = await unsealList(pins, {
      isDm: () => true,
      lookup: async (m) => { asked.push(m.message_id); return m.message_id === 'a' ? 'cached text' : null; },
    });
    expect(out.map((p) => p.content)).toEqual(['cached text', UNDECRYPTABLE, 'plain pin']);
    expect(asked).toEqual(['a', 'b']);
  });
});

describe('mergeSearchHits', () => {
  it('drops sealed gateway hits and adds local plaintext hits once', () => {
    const gw = [
      { message_id: 'r1', thread_id: 'room', content: 'room hit' },
      { message_id: 'd1', thread_id: 'dm', content: CT },
      { message_id: 'd2', thread_id: 'dm', content: 'x', e2e: true },
    ];
    const local = [
      { message_id: 'd1', thread_id: 'dm', content: 'the real text' },
      { message_id: 'r1', thread_id: 'room', content: 'dup' },
    ];
    const out = mergeSearchHits(gw, local, { isDm: (h) => h.thread_id === 'dm' });
    expect(out.map((h) => [h.message_id, h.content])).toEqual([['r1', 'room hit'], ['d1', 'the real text']]);
  });
});

describe('previewText (notifications)', () => {
  it('uses the notice for a placeholder or a sealed body', () => {
    expect(previewText({ content: UNDECRYPTABLE }, 'NOTICE')).toBe('NOTICE');
    expect(previewText({ content: CT, e2e: true }, 'NOTICE')).toBe('NOTICE');
    expect(previewText({ content: 'hi' }, 'NOTICE')).toBe('hi');
  });
});

describe('local DM plaintext search', () => {
  beforeEach(async () => { dmHistorySetEnabled(true); await dmHistoryClearAll(); });
  it('matches every word, case-insensitively', () => {
    expect(matchesQuery('Meet at the Old Mill', 'old mill')).toBe(true);
    expect(matchesQuery('Meet at the Old Mill', 'old barn')).toBe(false);
    expect(matchesQuery('anything', '  ')).toBe(false);
  });
  it('searches the cached plaintext, newest first', async () => {
    await dmHistorySave({ message_id: 'a', thread_id: 't', content: 'strike one', timestamp: '2026-01-01T00:00:00Z' });
    await dmHistorySave({ message_id: 'b', thread_id: 't', content: 'Strike two', timestamp: '2026-01-02T00:00:00Z' });
    await dmHistorySave({ message_id: 'c', thread_id: 't', content: 'ball', timestamp: '2026-01-03T00:00:00Z' });
    expect((await dmHistorySearch('strike')).map((r) => r.message_id)).toEqual(['b', 'a']);
    expect((await dmHistoryGet('c')).content).toBe('ball');
    expect(await dmHistoryGet('nope')).toBeNull();
  });
});

describe('main.js routes every DM surface through dmtext.js', () => {
  const src = readFileSync(new URL('./main.js', import.meta.url), 'utf8');
  it('history pages are unsealed before render', () => {
    expect(src).toMatch(/case "history": \{\s*const tid = event\.thread_id;\s*const msgs = _unsealHistory\(event\.messages, tid\);/);
    expect(src).toMatch(/case "local_history": \{\s*const tid = event\.thread_id;\s*const msgs = _unsealHistory\(event\.messages, tid\);/);
  });
  it('pins, search hits, fetched reply parents and notifications too', () => {
    expect(src).toMatch(/case "pins": \{[\s\S]{0,300}unsealList\(event\.pins/);
    expect(src).toMatch(/case "search_results":\s*_searchWithLocalDms\(event\)\.then\(renderSearchResults\)/);
    expect(src).toMatch(/case "message_fetched": \{\s*const m = event\.message\s*&& unsealCopy\(/);
    expect(src).toContain("showOsNotification(`${sender}`, previewText(msg, t('e2e.undecryptable')), id);");
  });
});
