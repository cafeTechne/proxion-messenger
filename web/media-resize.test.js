// media-resize.js — R59B: pure decision + plan logic (canvas path is
// browser-only and covered by the live smoke).
import { describe, it, expect } from 'vitest';
import {
    needsDownscale, downscalePlan, DOWNSCALE_TARGET_BYTES, DOWNSCALE_MAX_DIM,
} from './media-resize.js';

const mk = (size, type) => ({ size, type, name: 'x' });

describe('needsDownscale', () => {
    it('small files never downscale', () => {
        expect(needsDownscale(mk(100 * 1024, 'image/png'), 'room')).toBe(false);
        expect(needsDownscale(mk(524288, 'image/png'), 'room')).toBe(false);
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
        expect(DOWNSCALE_TARGET_BYTES).toBeLessThan(524288);
        expect(DOWNSCALE_MAX_DIM).toBeGreaterThanOrEqual(1024);
    });
});
