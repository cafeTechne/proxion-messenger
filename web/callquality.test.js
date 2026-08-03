// callquality.test.js — R81 P1: quality profiles + adaptive per-sender cap. Pure.
import { describe, it, expect } from 'vitest';
import { senderCap, resolveProfile, QUALITY_PROFILES, SCREEN_PROFILES, canEnableVideo, MAX_CONCURRENT_VIDEO } from './callquality.js';

describe('canEnableVideo (R83 concurrent-video cap)', () => {
    it('allows video below the cap and blocks at/above it', () => {
        expect(canEnableVideo(0)).toBe(true);
        expect(canEnableVideo(MAX_CONCURRENT_VIDEO - 1)).toBe(true);
        expect(canEnableVideo(MAX_CONCURRENT_VIDEO)).toBe(false);
        expect(canEnableVideo(MAX_CONCURRENT_VIDEO + 5)).toBe(false);
    });
    it('honors an explicit cap and tolerates junk', () => {
        expect(canEnableVideo(2, 3)).toBe(true);
        expect(canEnableVideo(3, 3)).toBe(false);
        expect(canEnableVideo(undefined)).toBe(true);
    });
});

describe('resolveProfile', () => {
    it('honors explicit profiles and resolves auto by call size', () => {
        expect(resolveProfile('high', true)).toBe('high');
        expect(resolveProfile('saver', false)).toBe('saver');
        expect(resolveProfile('auto', false)).toBe('high');       // 1:1 auto = generous
        expect(resolveProfile('auto', true)).toBe('standard');    // group auto = moderate
    });
});

describe('senderCap', () => {
    it('gives a 1:1 auto call the full high ceiling', () => {
        expect(senderCap({ profile: 'auto', isGroup: false }).maxBitrate).toBe(QUALITY_PROFILES.high.maxBitrate);
    });

    it('scales the group bitrate down as the mesh grows', () => {
        const two = senderCap({ profile: 'high', isGroup: true, participantCount: 2 }).maxBitrate;
        const four = senderCap({ profile: 'high', isGroup: true, participantCount: 4 }).maxBitrate;
        expect(two).toBe(QUALITY_PROFILES.high.maxBitrate);        // 1 other -> full
        expect(four).toBeLessThan(two);                           // 3 others -> divided
        expect(four).toBe(Math.round(QUALITY_PROFILES.high.maxBitrate / 3));
    });

    it('never drops below the floor', () => {
        const cap = senderCap({ profile: 'saver', isGroup: true, participantCount: 20 });
        expect(cap.maxBitrate).toBeGreaterThanOrEqual(150_000);
    });

    it('saver is much lower than high for the same call', () => {
        const hi = senderCap({ profile: 'high', isGroup: false }).maxBitrate;
        const lo = senderCap({ profile: 'saver', isGroup: false }).maxBitrate;
        expect(lo).toBeLessThan(hi);
    });

    it('uses the screen profile for screen share (more bitrate, fewer fps)', () => {
        const cam = senderCap({ profile: 'high', kind: 'camera' });
        const scr = senderCap({ profile: 'high', kind: 'screen' });
        expect(scr.maxBitrate).toBe(SCREEN_PROFILES.high.maxBitrate);
        expect(scr.maxFramerate).toBeLessThan(cam.maxFramerate);
    });
});
