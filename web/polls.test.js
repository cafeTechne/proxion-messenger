// polls.js — R59F: format/parse round-trip and malformed-input handling.
import { describe, it, expect } from 'vitest';
import { formatPoll, parsePoll, pollVoteChange, POLL_EMOJI, MAX_POLL_OPTIONS } from './polls.js';

describe('formatPoll', () => {
    it('formats question + options with keycap prefixes', () => {
        const s = formatPoll('Pizza night?', ['Friday', 'Saturday']);
        expect(s).toBe('📊 Pizza night?\n1️⃣ Friday\n2️⃣ Saturday');
    });
    it('trims, drops empties, caps at 5', () => {
        const s = formatPoll(' Q ', ['a', '', ' b ', 'c', 'd', 'e', 'f']);
        const lines = s.split('\n');
        expect(lines[0]).toBe('📊 Q');
        expect(lines.length).toBe(1 + MAX_POLL_OPTIONS);
    });
    it('rejects missing question or <2 options', () => {
        expect(formatPoll('', ['a', 'b'])).toBeNull();
        expect(formatPoll('Q', ['only'])).toBeNull();
        expect(formatPoll('Q', [])).toBeNull();
    });
});

describe('parsePoll', () => {
    it('round-trips formatPoll output', () => {
        const s = formatPoll('Best option?', ['one', 'two', 'three']);
        const p = parsePoll(s);
        expect(p.question).toBe('Best option?');
        expect(p.options.map(o => o.text)).toEqual(['one', 'two', 'three']);
        expect(p.options.map(o => o.emoji)).toEqual(POLL_EMOJI.slice(0, 3));
    });
    it('rejects non-polls and malformed polls', () => {
        expect(parsePoll('hello world')).toBeNull();
        expect(parsePoll('📊 question only')).toBeNull();          // no options
        expect(parsePoll('📊 q\n1️⃣ a')).toBeNull();               // one option
        expect(parsePoll('📊 q\n2️⃣ a\n1️⃣ b')).toBeNull();        // wrong order
        expect(parsePoll('📊 q\n1️⃣ a\n3️⃣ b')).toBeNull();        // gap
        expect(parsePoll('📊 q\n1️⃣\n2️⃣ b')).toBeNull();          // empty option
        expect(parsePoll('')).toBeNull();
        expect(parsePoll(null)).toBeNull();
    });
    it('a message merely mentioning 📊 mid-text is not a poll', () => {
        expect(parsePoll('check the 📊 chart')).toBeNull();
    });
});

describe('pollVoteChange', () => {
    const [A, B] = POLL_EMOJI;
    it('casts a first vote', () => {
        expect(pollVoteChange({}, A, 'me')).toEqual({ add: A, remove: [] });
    });
    it('moves an existing vote to the new option', () => {
        expect(pollVoteChange({ [A]: ['me', 'x'] }, B, 'me')).toEqual({ add: B, remove: [A] });
    });
    it('clears the vote when the same option is picked again', () => {
        expect(pollVoteChange({ [A]: ['me'] }, A, 'me')).toEqual({ add: null, remove: [A] });
    });
    it("ignores other people's votes and non-poll reactions", () => {
        expect(pollVoteChange({ [A]: ['x'], '👍': ['me'] }, B, 'me')).toEqual({ add: B, remove: [] });
    });
});
