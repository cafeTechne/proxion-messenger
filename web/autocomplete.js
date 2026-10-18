// autocomplete.js: shared behavior for the composer's suggestion popups (the
// @-mention and :shortcode: dropdowns). Each popup is a role="listbox" <ul>
// whose options are <li role="option">; the textarea keeps focus and points at
// the highlighted option with aria-activedescendant, so arrow keys, Enter/Tab
// and Escape work without moving focus and screen readers announce the option.
//
// The popup is position:fixed, so it has to be placed explicitly: it opens just
// above the composer input, aligned to the input's start edge (right edge in
// RTL), and flips below when there is no room above.

const GAP = 4;      // px between the input and the popup
const MARGIN = 8;   // px kept clear of the viewport edges

// Place `dd` against `anchor` (the message input). Measures after the popup is
// visible so its real width/height are known.
export function positionPopup(dd, anchor, win = globalThis.window) {
    if (!dd || !anchor || typeof anchor.getBoundingClientRect !== 'function' || !win) return;
    const r = anchor.getBoundingClientRect();
    const vw = win.innerWidth || 0;
    const vh = win.innerHeight || 0;
    const w = dd.offsetWidth || 0;
    const h = dd.offsetHeight || 0;
    const rtl = (anchor.closest?.('[dir="rtl"]') || win.document?.documentElement?.dir === 'rtl') ? true : false;
    let left = rtl ? r.right - w : r.left;
    left = Math.max(MARGIN, Math.min(left, vw - w - MARGIN));
    dd.style.left = `${Math.round(left)}px`;
    dd.style.right = 'auto';
    if (r.top - GAP - h >= MARGIN || r.top > vh - r.bottom) {
        // Above the input (the usual case: the composer sits at the bottom).
        dd.style.top = 'auto';
        dd.style.bottom = `${Math.round(vh - r.top + GAP)}px`;
    } else {
        dd.style.bottom = 'auto';
        dd.style.top = `${Math.round(r.bottom + GAP)}px`;
    }
}

// Show the popup and wire the input to it.
export function openListbox(input, dd) {
    if (!dd) return;
    dd.style.display = 'block';
    if (input?.setAttribute) {
        input.setAttribute('aria-controls', dd.id);
        input.setAttribute('aria-autocomplete', 'list');
    }
    positionPopup(dd, input);
}

// Hide the popup. Only clears the input's pointers when they belong to this
// popup: both popups share one input, and closing the idle one must not unhook
// the one that is open.
export function closeListbox(input, dd) {
    if (dd) dd.style.display = 'none';
    if (!input?.getAttribute || !dd) return;
    if (input.getAttribute('aria-controls') === dd.id) {
        input.removeAttribute('aria-activedescendant');
        input.removeAttribute('aria-controls');
    }
}

// Highlight option `idx` and point the input at it.
export function setActiveOption(input, items, idx) {
    const list = Array.from(items || []);
    list.forEach((el, i) => {
        const on = i === idx;
        el.classList.toggle('focused', on);
        el.setAttribute?.('aria-selected', on ? 'true' : 'false');
    });
    const active = list[idx];
    if (active && input?.setAttribute) {
        if (active.id) input.setAttribute('aria-activedescendant', active.id);
        active.scrollIntoView?.({ block: 'nearest' });
    }
}

// A fresh <li role="option"> for row `i` of popup `prefix`.
export function makeOption(prefix, i) {
    const li = document.createElement('li');
    li.id = `${prefix}-opt-${i}`;
    li.className = 'ac-option mention-option' + (i === 0 ? ' focused' : '');
    li.setAttribute?.('role', 'option');
    li.setAttribute?.('aria-selected', i === 0 ? 'true' : 'false');
    li.dataset.idx = String(i);
    return li;
}

// Keep focus in the input when an option is clicked (mousedown would blur it).
export function keepInputFocus(dd) {
    dd?.addEventListener?.('mousedown', (e) => e.preventDefault());
}

// True while either composer popup is showing. The composer's own Enter-to-send
// handler is registered before the popups', so it asks this first: Enter picks
// the highlighted suggestion instead of sending a half-typed "@Bo".
export function isPopupOpen(doc = globalThis.document) {
    return ['mention-dropdown', 'emoji-dropdown'].some((id) => {
        const dd = doc?.getElementById?.(id);
        return !!dd && dd.style.display !== 'none' && dd.style.display !== '';
    });
}
