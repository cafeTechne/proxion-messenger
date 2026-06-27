// Tests for the F3 consistent-state helpers (states.js).
import { describe, it, expect, beforeEach } from 'vitest';
import { inlineNotice, feedEmptyState } from './states.js';

// The suite runs in the node environment (no jsdom), so stub createElement the
// same way the other DOM-touching tests do and assert on the generated markup.
beforeEach(() => {
    global.document = { createElement: () => ({ className: '', innerHTML: '' }) };
});

describe('inlineNotice', () => {
    it('renders a .state-msg with the kind class (default empty)', () => {
        expect(inlineNotice('No members found.')).toBe(
            '<p class="state-msg state-empty">No members found.</p>');
    });

    it('supports loading and error kinds', () => {
        expect(inlineNotice('Loading…', 'loading')).toContain('state-loading');
        expect(inlineNotice('Could not load history.', 'error')).toContain('state-error');
    });

    it('escapes the message (no raw HTML injection)', () => {
        const html = inlineNotice('<img src=x onerror=alert(1)>', 'error');
        expect(html).not.toContain('<img');
        expect(html).toContain('&lt;img');
    });
});

describe('feedEmptyState', () => {
    it('builds an .empty-state element with icon, title and hint', () => {
        const el = feedEmptyState({ title: 'No messages yet.', hint: 'Be the first to say hello.' });
        expect(el.className).toBe('empty-state');
        expect(el.innerHTML).toContain('class="empty-state-icon"');
        expect(el.innerHTML).toContain('<svg');
        expect(el.innerHTML).toContain('class="empty-state-title">No messages yet.</div>');
        expect(el.innerHTML).toContain('class="empty-state-hint">Be the first to say hello.</div>');
    });

    it('omits the hint node when no hint is given', () => {
        const el = feedEmptyState({ title: 'Nothing here.' });
        expect(el.innerHTML).not.toContain('empty-state-hint');
    });

    it('escapes the title (no raw HTML injection)', () => {
        const el = feedEmptyState({ title: '<b>x</b>' });
        expect(el.innerHTML).toContain('&lt;b&gt;');
        expect(el.innerHTML).not.toContain('<b>x</b>');
    });
});
