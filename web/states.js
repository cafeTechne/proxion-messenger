// Consistent empty / loading / error state markup (ROADMAP_2 F3).
//
// Replaces the ad-hoc inline-styled "Loading..." / "No X" strings that were
// scattered across main.js and the module leaves — each with its own #94a3b8
// inline color, a stray <em>, and an inconsistent ellipsis ("..." vs "…") —
// with one tokenized, themeable pattern. Pure functions, only escHtml as a dep:
// callers assign the returned string to innerHTML, or append the element from
// feedEmptyState().

import { escHtml } from './util.js';
import { icon as svgIcon } from './icons.js';

// Small inline notice for lists / popovers (members list, forward list, pins,
// edit-history load). kind ∈ "empty" | "loading" | "error" — drives the
// .state-* class so errors render in the danger colour, etc.
export function inlineNotice(message, kind = 'empty') {
    return `<p class="state-msg state-${kind}">${escHtml(String(message))}</p>`;
}

// Chat-bubble glyph used by the main-feed empty state.
const _FEED_ICON = svgIcon('chat-bubble', { size: 48 });

// Rich centred empty-state for the main message feed (icon + title + hint).
// Returns a detached element so the caller controls insertion.
export function feedEmptyState({ title = 'Nothing here yet.', hint = '', icon = _FEED_ICON } = {}) {
    const el = document.createElement('div');
    el.className = 'empty-state';
    el.innerHTML =
        `<div class="empty-state-icon">${icon}</div>` +
        `<div class="empty-state-title">${escHtml(title)}</div>` +
        (hint ? `<div class="empty-state-hint">${escHtml(hint)}</div>` : '');
    return el;
}

// Feed status notice (#feed-notice): "you're offline" or "couldn't load" in
// place of a blank pane, with an optional Retry button. Replaces any previous
// notice in the feed; returns the element.
export function showFeedNotice(feed, message, { onRetry, retryLabel = 'Retry' } = {}) {
    if (!feed) return null;
    feed.querySelector?.('#feed-notice')?.remove();
    const el = document.createElement('div');
    el.id = 'feed-notice';
    el.className = 'feed-notice';
    el.setAttribute?.('role', 'status');
    el.innerHTML = inlineNotice(message, onRetry ? 'error' : 'empty');
    if (onRetry) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'state-cta';
        btn.textContent = retryLabel;
        btn.addEventListener('click', () => { el.remove(); onRetry(); });
        el.appendChild(btn);
    }
    feed.appendChild(el);
    return el;
}
