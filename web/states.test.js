// Tests for the F3 consistent-state helpers (states.js).
import { describe, it, expect, beforeEach } from 'vitest';
import { inlineNotice, feedEmptyState, showFeedNotice } from './states.js';

// The suite runs in the node environment (no jsdom), so stub createElement the
// same way the other DOM-touching tests do and assert on the generated markup.
beforeEach(() => {
    global.document = {
        createElement: (tag) => ({
            tagName: tag, className: '', innerHTML: '', textContent: '', children: [], listeners: {},
            appendChild(c) { this.children.push(c); },
            addEventListener(ev, fn) { this.listeners[ev] = fn; },
        }),
    };
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

describe('showFeedNotice', () => {
    function mkNode() {
        const n = { children: [], attrs: {}, listeners: {}, removed: false,
            setAttribute(k, v) { n.attrs[k] = v; },
            appendChild(c) { n.children.push(c); },
            addEventListener(type, fn) { n.listeners[type] = fn; },
            remove() { n.removed = true; } };
        return n;
    }
    beforeEach(() => { global.document = { createElement: () => mkNode() }; });

    it('shows a plain status notice without a button', () => {
        const feed = mkNode();
        feed.querySelector = () => null;
        const el = showFeedNotice(feed, 'You are offline.');
        expect(feed.children).toEqual([el]);
        expect(el.id).toBe('feed-notice');
        expect(el.attrs.role).toBe('status');
        expect(el.innerHTML).toContain('state-empty');
        expect(el.children).toHaveLength(0);
    });

    it('adds a Retry button that removes the notice and calls back', () => {
        const feed = mkNode();
        feed.querySelector = () => null;
        let retried = 0;
        const el = showFeedNotice(feed, "Couldn't load messages.", { onRetry: () => retried++, retryLabel: 'Retry' });
        expect(el.innerHTML).toContain('state-error');
        const btn = el.children[0];
        expect(btn.textContent).toBe('Retry');
        btn.listeners.click();
        expect(el.removed).toBe(true);
        expect(retried).toBe(1);
    });

    it('replaces an existing notice', () => {
        const old = mkNode();
        const feed = mkNode();
        feed.querySelector = (sel) => (sel === '#feed-notice' ? old : null);
        showFeedNotice(feed, 'x');
        expect(old.removed).toBe(true);
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

    it('adds action buttons that run their handler', () => {
        let clicked = 0;
        const el = feedEmptyState({
            title: 'You left the room.',
            actions: [{ label: 'Create room', variant: 'accent', onClick: () => clicked++ }, { label: 'Add contact' }],
        });
        const row = el.children[0];
        expect(row.className).toContain('empty-state-actions');
        expect(row.children.map(b => b.textContent)).toEqual(['Create room', 'Add contact']);
        expect(row.children[0].className).toBe('btn btn--accent');
        expect(row.children[1].className).toBe('btn btn--slate');
        row.children[0].listeners.click();
        expect(clicked).toBe(1);
    });

    it('adds no action row by default', () => {
        expect(feedEmptyState({ title: 'x' }).children).toHaveLength(0);
    });

    it('escapes the title (no raw HTML injection)', () => {
        const el = feedEmptyState({ title: '<b>x</b>' });
        expect(el.innerHTML).toContain('&lt;b&gt;');
        expect(el.innerHTML).not.toContain('<b>x</b>');
    });
});
