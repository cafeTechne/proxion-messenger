// meme.js — R60B: caption layout math (pure parts).
import { describe, it, expect } from 'vitest';
import { wrapCaption, captionFontPx, MEME_MAX_DIM } from './meme.js';

describe('wrapCaption', () => {
    it('wraps greedily at the character budget', () => {
        expect(wrapCaption('one two three four', 9)).toEqual(['one two', 'three', 'four']);
    });
    it('keeps a single overlong word on its own line', () => {
        expect(wrapCaption('supercalifragilistic ok', 10)).toEqual(['supercalifragilistic', 'ok']);
    });
    it('caps at maxLines with an ellipsis', () => {
        const lines = wrapCaption('a b c d e f g h', 1, 3);
        expect(lines).toHaveLength(3);
        expect(lines[2].endsWith('…')).toBe(true);
    });
    it('handles empty and whitespace input', () => {
        expect(wrapCaption('', 10)).toEqual([]);
        expect(wrapCaption('   ', 10)).toEqual([]);
    });
});

describe('captionFontPx', () => {
    it('scales with height and shrinks for long captions', () => {
        const short = captionFontPx(800, 10);
        const medium = captionFontPx(800, 30);
        const long = captionFontPx(800, 60);
        expect(short).toBeGreaterThan(medium);
        expect(medium).toBeGreaterThan(long);
        expect(short).toBe(100);
    });
    it('never drops below the readable floor', () => {
        expect(captionFontPx(50, 100)).toBeGreaterThanOrEqual(14);
    });
    it('sane max dimension export', () => {
        expect(MEME_MAX_DIM).toBeGreaterThanOrEqual(512);
    });
});
