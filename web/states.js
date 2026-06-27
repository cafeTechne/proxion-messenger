// Consistent empty / loading / error state markup (ROADMAP_2 F3).
//
// Replaces the ad-hoc inline-styled "Loading..." / "No X" strings that were
// scattered across main.js and the module leaves — each with its own #94a3b8
// inline color, a stray <em>, and an inconsistent ellipsis ("..." vs "…") —
// with one tokenized, themeable pattern. Pure functions, only escHtml as a dep:
// callers assign the returned string to innerHTML, or append the element from
// feedEmptyState().

import { escHtml } from './util.js';

// Small inline notice for lists / popovers (members list, forward list, pins,
// edit-history load). kind ∈ "empty" | "loading" | "error" — drives the
// .state-* class so errors render in the danger colour, etc.
export function inlineNotice(message, kind = 'empty') {
    return `<p class="state-msg state-${kind}">${escHtml(String(message))}</p>`;
}

// Chat-bubble glyph used by the main-feed empty state.
const _FEED_ICON =
    '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" ' +
    'stroke-width="1.5" stroke="currentColor" aria-hidden="true" width="48" height="48">' +
    '<path stroke-linecap="round" stroke-linejoin="round" d="M2.25 12.76c0 1.6 1.123 2.994 ' +
    '2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.076-4.076a1.526 1.526 0 0 1 1.037-.443 ' +
    '48.282 48.282 0 0 0 5.68-.494c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 ' +
    '48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z"/></svg>';

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
