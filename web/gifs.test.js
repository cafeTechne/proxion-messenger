// gifs.js — personal GIF/meme tray: content-addressed favorites in IndexedDB.
import 'fake-indexeddb/auto';
import { describe, it, expect, beforeEach } from 'vitest';
import {
    contentId, evictOverCap, fileFromFavorite, saveFavorite, sortByRecency,
    listFavorites, removeFavorite, touchFavorite, MAX_FAVORITES,
} from './gifs.js';

// A 1×1 transparent GIF, base64.
const GIF_B64 = 'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

beforeEach(async () => {
    const rows = await listFavorites();
    for (const r of rows) await removeFavorite(r.id);
});

describe('contentId (pure)', () => {
    it('is deterministic and content-addressed', async () => {
        expect(await contentId(GIF_B64)).toBe(await contentId(GIF_B64));
        expect(await contentId(GIF_B64)).not.toBe(await contentId(GIF_B64 + 'x'));
        expect(await contentId(GIF_B64)).toMatch(/^[0-9a-f]{64}$/);
    });
});

describe('evictOverCap (pure)', () => {
    it('returns [] when under the cap', () => {
        expect(evictOverCap([{ id: 'a', addedAt: 1 }], 5)).toEqual([]);
        expect(evictOverCap(null, 5)).toEqual([]);
    });
    it('evicts least-recently-used first to make room for one more', () => {
        const rows = [
            { id: 'old-unused', addedAt: 1, lastUsedAt: 0 },
            { id: 'old-but-used', addedAt: 2, lastUsedAt: 100 },
            { id: 'new', addedAt: 50, lastUsedAt: 0 },
        ];
        // cap 3, already 3 rows → must evict exactly 1: the LRU one
        expect(evictOverCap(rows, 3)).toEqual(['old-unused']);
    });
});

describe('fileFromFavorite (pure)', () => {
    it('round-trips base64 into a typed File', async () => {
        const f = fileFromFavorite({ filename: 'meme.gif', mime: 'image/gif', data_b64: GIF_B64 });
        expect(f.name).toBe('meme.gif');
        expect(f.type).toBe('image/gif');
        const bytes = new Uint8Array(await f.arrayBuffer());
        expect(bytes[0]).toBe(0x47); // 'G'
        expect(bytes[1]).toBe(0x49); // 'I'
        expect(bytes[2]).toBe(0x46); // 'F'
    });
});

describe('favorites store', () => {
    it('saves, lists, and dedupes by content', async () => {
        expect(await saveFavorite({ filename: 'a.gif', mime: 'image/gif', data_b64: GIF_B64 })).toBe('saved');
        expect(await saveFavorite({ filename: 'b.gif', mime: 'image/gif', data_b64: GIF_B64 })).toBe('exists');
        const rows = await listFavorites();
        expect(rows.length).toBe(1);
        expect(rows[0].filename).toBe('a.gif');
    });

    it('removes favorites', async () => {
        await saveFavorite({ filename: 'a.gif', mime: 'image/gif', data_b64: GIF_B64 });
        const [row] = await listFavorites();
        await removeFavorite(row.id);
        expect(await listFavorites()).toEqual([]);
    });

    it('touch bumps lastUsedAt and useCount', async () => {
        await saveFavorite({ filename: 'a.gif', mime: 'image/gif', data_b64: GIF_B64 });
        let [a] = await listFavorites();
        expect(a.useCount).toBe(0);
        await touchFavorite(a.id);
        [a] = await listFavorites();
        expect(a.useCount).toBe(1);
        expect(a.lastUsedAt).toBeGreaterThan(0);
    });

    it('sortByRecency (pure): most-recently-used first, deterministic ties', () => {
        const rows = [
            { id: 'b', addedAt: 10, lastUsedAt: 0, useCount: 0 },
            { id: 'a', addedAt: 5, lastUsedAt: 100, useCount: 2 },
            { id: 'c', addedAt: 10, lastUsedAt: 0, useCount: 1 },
        ];
        expect(sortByRecency(rows).map(r => r.id)).toEqual(['a', 'c', 'b']);
    });

    it('exports a sane cap', () => {
        expect(MAX_FAVORITES).toBeGreaterThan(50);
    });
});
