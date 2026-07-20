// emoji.js — R59C: shortcode matcher, trigger detection, caret math, map sanity.
import { describe, it, expect } from 'vitest';
import {
    EMOJI_MAP, matchShortcodes, findShortcodeStart, applyShortcode,
} from './emoji.js';

describe('EMOJI_MAP', () => {
    it('names match the autocomplete trigger charset', () => {
        for (const name of Object.keys(EMOJI_MAP)) {
            expect(name).toMatch(/^[a-z0-9_+-]+$/);
        }
    });
    it('has a useful size and the staples', () => {
        expect(Object.keys(EMOJI_MAP).length).toBeGreaterThan(150);
        expect(EMOJI_MAP.joy).toBe('😂');
        expect(EMOJI_MAP['+1']).toBe('👍');
        expect(EMOJI_MAP.fire).toBe('🔥');
    });
});

describe('matchShortcodes', () => {
    it('prefix matches come before substring matches, alphabetical', () => {
        const names = matchShortcodes('hea', 20).map(m => m.name);
        expect(names[0].startsWith('hea')).toBe(true);
        const firstInfix = names.findIndex(n => !n.startsWith('hea'));
        if (firstInfix !== -1) {
            expect(names.slice(firstInfix).every(n => n.includes('hea'))).toBe(true);
        }
    });
    it('respects the limit and empty query', () => {
        expect(matchShortcodes('s', 3)).toHaveLength(3);
        expect(matchShortcodes('')).toEqual([]);
        expect(matchShortcodes('zzzznope')).toEqual([]);
    });
    it('is case-insensitive', () => {
        expect(matchShortcodes('JOY', 5).some(m => m.name === 'joy')).toBe(true);
    });
});

describe('findShortcodeStart', () => {
    it('finds a colon at start or after whitespace with ≥2 chars typed', () => {
        expect(findShortcodeStart(':jo', 3)).toBe(0);
        expect(findShortcodeStart('hi :fi', 6)).toBe(3);
    });
    it('rejects short queries, mid-word colons, and non-trigger text', () => {
        expect(findShortcodeStart(':j', 2)).toBe(-1);          // only 1 char
        expect(findShortcodeStart('http://ex', 9)).toBe(-1);   // colon mid-word
        expect(findShortcodeStart('hello', 5)).toBe(-1);       // no colon
        expect(findShortcodeStart('a b c', 5)).toBe(-1);
        expect(findShortcodeStart('say :hi there', 13)).toBe(-1); // space broke the run
    });
});

describe('applyShortcode', () => {
    it('replaces the trigger and positions the caret after emoji + space', () => {
        const r = applyShortcode('hi :joy', 7, 3, '😂');
        expect(r.text).toBe('hi 😂 ');
        expect(r.caret).toBe(3 + '😂 '.length);
    });
    it('preserves text after the caret', () => {
        const r = applyShortcode(':fir and more', 4, 0, '🔥');
        expect(r.text).toBe('🔥  and more');
        expect(r.caret).toBe('🔥 '.length);
    });
});

describe('matchShortcodes with custom room emoji (R60A)', () => {
    const CUSTOM = {
        blobheart: { mime: 'image/png', data_b64: 'AA==' },
        fire: { mime: 'image/png', data_b64: 'BB==' },   // shadows built-in
    };
    it('custom names rank first and carry the image payload', () => {
        const m = matchShortcodes('blob', 8, CUSTOM);
        expect(m[0]).toMatchObject({ name: 'blobheart', custom: true, mime: 'image/png' });
    });
    it('custom shadows a same-named built-in (no duplicate)', () => {
        const m = matchShortcodes('fire', 8, CUSTOM);
        const fires = m.filter(x => x.name === 'fire');
        expect(fires).toHaveLength(1);
        expect(fires[0].custom).toBe(true);
    });
    it('null custom behaves exactly as before', () => {
        const m = matchShortcodes('fire', 8, null);
        expect(m[0]).toMatchObject({ name: 'fire', emoji: '🔥' });
    });
});
