// media-resize.js — R59B: pure decision + plan logic (canvas path is
// browser-only and covered by the live smoke).
import { describe, it, expect } from 'vitest';
import {
    needsDownscale, downscalePlan, DOWNSCALE_TARGET_BYTES, DOWNSCALE_MAX_DIM,
    normalizeMediaDims, measureMedia, MAX_MEDIA_DIM,
    inlineFileLimit, ROOM_FILE_BYTES, DM_INLINE_FILE_BYTES,
} from './media-resize.js';

const mk = (size, type) => ({ size, type, name: 'x' });

describe('needsDownscale', () => {
    it('small files never downscale', () => {
        expect(needsDownscale(mk(100 * 1024, 'image/png'), 'room')).toBe(false);
        expect(needsDownscale(mk(524288, 'image/png'), 'room')).toBe(false);
        // F25: a 1.5 MB photo fits a room as is.
        expect(needsDownscale(mk(1536 * 1024, 'image/jpeg'), 'room')).toBe(false);
    });
    it('oversized still images downscale for rooms, not DMs', () => {
        const big = mk(3 * 1024 * 1024, 'image/jpeg');
        expect(needsDownscale(big, 'room')).toBe(true);
        expect(needsDownscale(big, 'local_room')).toBe(true);
        expect(needsDownscale(big, 'dm')).toBe(false);
        expect(needsDownscale(big, 'local_dm')).toBe(false);
    });
    it('animated-capable GIFs and non-images are excluded', () => {
        expect(needsDownscale(mk(3 * 1024 * 1024, 'image/gif'), 'room')).toBe(false);
        expect(needsDownscale(mk(3 * 1024 * 1024, 'video/mp4'), 'room')).toBe(false);
        expect(needsDownscale(mk(3 * 1024 * 1024, ''), 'room')).toBe(false);
        expect(needsDownscale(null, 'room')).toBe(false);
    });
});

describe('inlineFileLimit (F25)', () => {
    it('rooms take 2 MB inline; DMs keep 512 KB and go chunked above it', () => {
        expect(ROOM_FILE_BYTES).toBe(2 * 1024 * 1024);
        expect(inlineFileLimit('room')).toBe(ROOM_FILE_BYTES);
        expect(inlineFileLimit('local_room')).toBe(ROOM_FILE_BYTES);
        expect(inlineFileLimit('dm')).toBe(DM_INLINE_FILE_BYTES);
        expect(inlineFileLimit('local_dm', 16)).toBe(512 * 1024 - 16);
    });
});

describe('downscalePlan', () => {
    it('steps quality within a size, then halves dimensions, floor 256', () => {
        const plan = downscalePlan(1024);
        expect(plan.slice(0, 3)).toEqual([
            { dim: 1024, q: 0.85 }, { dim: 1024, q: 0.7 }, { dim: 1024, q: 0.5 },
        ]);
        expect(plan[3].dim).toBe(512);
        expect(plan[plan.length - 1].dim).toBeGreaterThanOrEqual(256);
        expect(plan.every(s => s.dim >= 256)).toBe(true);
    });
    it('sane exported constants', () => {
        expect(DOWNSCALE_TARGET_BYTES).toBeLessThan(ROOM_FILE_BYTES);
        expect(DOWNSCALE_MAX_DIM).toBeGreaterThanOrEqual(1024);
    });
});

describe('normalizeMediaDims / measureMedia', () => {
    it('keeps integer pairs in range and drops everything else', () => {
        expect(normalizeMediaDims(640, 480)).toEqual({ width: 640, height: 480 });
        expect(normalizeMediaDims(1, MAX_MEDIA_DIM)).toEqual({ width: 1, height: MAX_MEDIA_DIM });
        for (const [w, h] of [[0, 1], [1, 0], [-1, 5], [MAX_MEDIA_DIM + 1, 5], [1.5, 2], ['640', 480], [null, 4], [4, undefined], [NaN, 3]]) {
            expect(normalizeMediaDims(w, h)).toBeNull();
        }
    });

    it('measures an image through createImageBitmap and closes the bitmap', async () => {
        let closed = false;
        globalThis.createImageBitmap = async () => ({ width: 800, height: 600, close() { closed = true; } });
        try {
            expect(await measureMedia({ type: 'image/png' })).toEqual({ width: 800, height: 600 });
            expect(closed).toBe(true);
        } finally { delete globalThis.createImageBitmap; }
    });

    it('returns null for non-media files and undecodable images, never throws', async () => {
        expect(await measureMedia({ type: 'application/pdf' })).toBeNull();
        expect(await measureMedia(null)).toBeNull();
        globalThis.createImageBitmap = async () => { throw new Error('bad'); };
        try {
            expect(await measureMedia({ type: 'image/jpeg' })).toBeNull();
        } finally { delete globalThis.createImageBitmap; }
    });
});
