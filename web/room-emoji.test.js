// room-emoji.js — R59G: token replacement safety, map hygiene.
import { describe, it, expect } from 'vitest';
import { applyRoomEmoji, setRoomEmoji, getRoomEmoji, EMOJI_NAME_RE } from './room-emoji.js';

const MAP = {
    partyparrot: { mime: 'image/gif', data_b64: 'QUJD' },
    ship_it: { mime: 'image/png', data_b64: 'REVG' },
};

describe('applyRoomEmoji (pure)', () => {
    it('replaces known :name: tokens with img tags', () => {
        const html = applyRoomEmoji('deploy :ship_it: now', MAP);
        expect(html).toContain('<img class="custom-emoji"');
        expect(html).toContain('data:image/png;base64,REVG');
        expect(html).toContain('alt=":ship_it:"');
    });
    it('leaves unknown names and stray colons untouched', () => {
        expect(applyRoomEmoji('a :nope: b', MAP)).toBe('a :nope: b');
        expect(applyRoomEmoji('10:30 meeting', MAP)).toBe('10:30 meeting');
        expect(applyRoomEmoji('', MAP)).toBe('');
        expect(applyRoomEmoji('x', {})).toBe('x');
        expect(applyRoomEmoji('x', null)).toBe('x');
    });
    it('multiple tokens in one message all resolve', () => {
        const html = applyRoomEmoji(':partyparrot: :ship_it: :partyparrot:', MAP);
        expect(html.match(/<img/g)).toHaveLength(3);
    });
    it('cannot be used for injection — names are charset-bound, values from the map only', () => {
        // A hostile "name" never matches the token regex…
        expect(applyRoomEmoji(':<script>:', MAP)).toBe(':<script>:');
        // …and escaped content around tokens stays escaped.
        const html = applyRoomEmoji('&lt;b&gt; :ship_it:', MAP);
        expect(html.startsWith('&lt;b&gt; ')).toBe(true);
    });
});

describe('setRoomEmoji / getRoomEmoji', () => {
    it('stores per-room and filters invalid names defensively', () => {
        setRoomEmoji('room-a', [
            { name: 'valid_one', mime: 'image/png', data_b64: 'AA==' },
            { name: 'Bad Name', mime: 'image/png', data_b64: 'AA==' },
            { name: 'x', mime: 'image/png', data_b64: 'AA==' },
        ]);
        expect(Object.keys(getRoomEmoji('room-a'))).toEqual(['valid_one']);
        expect(getRoomEmoji('room-other')).toEqual({});
    });
    it('name regex matches the server contract', () => {
        expect(EMOJI_NAME_RE.test('ab')).toBe(true);
        expect(EMOJI_NAME_RE.test('a'.repeat(32))).toBe(true);
        expect(EMOJI_NAME_RE.test('a'.repeat(33))).toBe(false);
        expect(EMOJI_NAME_RE.test('UPPER')).toBe(false);
        expect(EMOJI_NAME_RE.test('has-dash')).toBe(false);
    });
});
