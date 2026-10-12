// focus-trap.js — centralized modal a11y: focus restore + Tab-trapping.
//
// The app has ~20 modals, all toggled by flipping style.display between
// "flex" and "none" (a few, like #integrations-modal, are created on the
// fly). Rather than retrofit every open/close site, we observe the DOM: when
// a dialog becomes visible we (1) remember what had focus, (2) move focus to
// the first focusable control inside it, (3) trap Tab within it and (4) make
// the rest of the page inert. When it hides again we restore focus to wherever
// it was.
//
// Self-contained: installFocusTrap() wires a single MutationObserver and
// returns nothing. Idempotent — calling twice is a no-op after the first.

const FOCUSABLE = [
    'a[href]', 'button:not([disabled])', 'input:not([disabled])',
    'select:not([disabled])', 'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
].join(',');

// Matches every modal in the app: role=dialog is the canonical marker; the
// id suffix catches the handful created without the attribute.
const MODAL_SEL = '[role="dialog"], [id$="-modal"]';

function isVisible(el) {
    // offsetParent is null for display:none (and detached) nodes; it stays
    // set for our position:fixed overlays whenever they're shown.
    if (el.offsetParent !== null) return true;
    // position:fixed elements have a null offsetParent even when visible, so
    // fall back to the computed display for those.
    return getComputedStyle(el).display !== 'none';
}

function focusableWithin(modal) {
    return Array.from(modal.querySelectorAll(FOCUSABLE))
        .filter(el => isVisible(el) && el.getAttribute('aria-hidden') !== 'true');
}

// Where focus lands when a dialog opens: a visible control marked
// data-autofocus (a confirm's primary button, so Enter does the expected
// thing), else the first focusable control.
export function initialFocusTarget(items) {
    return items.find(el => el.hasAttribute('data-autofocus')) || items[0] || null;
}

// ---- Inert background ----
//
// aria-modal alone is not honoured by every screen reader, and it does nothing
// for pointer users clicking through gaps in a scrim. While a dialog is open
// every region that does not hold the topmost dialog gets `inert` (a dialog
// stacked under another one included). Body-level toasts and live regions stay
// live so they are still announced and their buttons still work. Only nodes we
// marked are released, so something inert for another reason stays inert.

const KEEP_LIVE_SEL = '#toast-container, [aria-live], [role="status"], [role="alert"], [role="log"], script, style, template';
const INERT_MARK = 'data-trap-inert';

export function syncBackgroundInert(body, top) {
    if (!body || typeof body.querySelectorAll !== 'function') return;
    // Siblings of the top dialog and of each of its ancestors up to <body>:
    // that is everything except the dialog itself, even when it sits inside
    // #app (the meme dialog does).
    const want = new Set();
    for (let n = top; n && n !== body && n.parentElement; n = n.parentElement) {
        for (const sib of Array.from(n.parentElement.children)) {
            if (sib !== n && !sib.matches(KEEP_LIVE_SEL)) want.add(sib);
        }
    }
    for (const el of Array.from(body.querySelectorAll(`[${INERT_MARK}]`))) {
        if (!want.has(el)) { el.inert = false; el.removeAttribute(INERT_MARK); }
    }
    for (const el of want) {
        if (!el.inert) { el.inert = true; el.setAttribute(INERT_MARK, ''); }
    }
}

// ---- Focus trap and restore ----
//
// One entry per open dialog, in opening order, each with the element that had
// focus when it opened. Closing a dialog returns focus to its own opener (the
// button inside Settings that opened a confirm, say), so a stack of dialogs
// unwinds the way it was built.

const _stack = [];
let installed = false;
let _release = null;

function _focus(el) {
    if (el && typeof el.focus === 'function') {
        try { el.focus(); } catch { /* detached */ }
    }
}

function _isDisplayNone(el) {
    return !!(el.hidden || (el.style && el.style.display === 'none'));
}

// Close a dialog with focus moved out first: background live again, focus back
// on the opener, then hide. Hiding first would leave focus inside a hidden
// subtree for a moment, where browsers drop it to <body>.
export function closeDialog(modal) {
    if (!modal) return;
    if (_release) _release(modal);
    modal.style.display = 'none';
}

export function installFocusTrap() {
    if (installed || typeof document === 'undefined') return;
    installed = true;

    const top = () => (_stack.length ? _stack[_stack.length - 1].modal : null);

    function onKeydown(e) {
        const trapped = top();
        if (e.key !== 'Tab' || !trapped) return;
        const items = focusableWithin(trapped);
        if (!items.length) { e.preventDefault(); return; }
        const first = items[0];
        const last = items[items.length - 1];
        const active = document.activeElement;
        // If focus has escaped the modal entirely, pull it back in.
        if (!trapped.contains(active)) {
            e.preventDefault();
            first.focus();
            return;
        }
        if (e.shiftKey && active === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && active === last) {
            e.preventDefault();
            first.focus();
        }
    }
    document.addEventListener('keydown', onKeydown, true);

    function trap(modal) {
        if (top() === modal) return;
        const i = _stack.findIndex(e => e.modal === modal);
        if (i >= 0) _stack.splice(i, 1);
        _stack.push({ modal, opener: document.activeElement });
        syncBackgroundInert(document.body, modal);
        const items = focusableWithin(modal);
        const target = initialFocusTarget(items) || modal;
        // Give a container tabindex so it can hold focus if it has no controls.
        if (target === modal && !modal.hasAttribute('tabindex')) modal.tabIndex = -1;
        // Defer to let any open-time innerHTML settle before focusing.
        requestAnimationFrame(() => {
            if (top() === modal) _focus(target);
        });
    }

    function release(modal) {
        const i = _stack.findIndex(e => e.modal === modal);
        if (i < 0) return;
        const [{ opener }] = _stack.splice(i, 1);
        const wasTop = i === _stack.length;
        if (!wasTop) {
            // A dialog under the top one closed. If the dialog above was opened
            // from inside it, hand that dialog the older opener instead.
            const above = _stack[i];
            if (above && (!above.opener || modal.contains(above.opener))) above.opener = opener;
            return;
        }
        const next = top();
        // Order matters: un-inert first, an inert element cannot take focus.
        syncBackgroundInert(document.body, next);
        const usable = opener && opener !== document.body && document.contains(opener)
            && !modal.contains(opener) && (!next || next.contains(opener));
        if (usable) _focus(opener);
        else if (next) _focus(focusableWithin(next)[0] || next);
        else if (modal.contains(document.activeElement)) _focus(document.body);
    }
    _release = release;

    function sync(modal) {
        // Cheap check first: a closing dialog has an inline display:none, and
        // reading it does not force a style recalc before focus moves.
        if (_isDisplayNone(modal) || !isVisible(modal)) release(modal);
        else trap(modal);
    }

    const observer = new MutationObserver(mutations => {
        for (const m of mutations) {
            if (m.type === 'attributes' && m.target.matches?.(MODAL_SEL)) {
                sync(m.target);
            }
            if (m.type === 'childList') {
                m.addedNodes.forEach(n => {
                    if (n.nodeType === 1 && n.matches?.(MODAL_SEL)) sync(n);
                });
                m.removedNodes.forEach(n => {
                    if (n.nodeType === 1 && _stack.some(e => e.modal === n)) release(n);
                });
            }
        }
    });
    observer.observe(document.body, {
        subtree: true, childList: true,
        attributes: true, attributeFilter: ['style', 'class', 'hidden'],
    });

    // Catch any modal already visible at install time.
    document.querySelectorAll(MODAL_SEL).forEach(el => { if (isVisible(el)) trap(el); });
}

// ---- Escape: close only the topmost dialog ----
//
// Escape used to hide every visible dialog at once, which dismissed Settings
// under a stacked confirm, the required sign-in/onboarding gates, and skipped
// cancel callbacks (confirm onCancel, attachment promise). closeTopmostDialog
// closes just the top one through its own cancel control.
//
// getStyle is injectable for tests (default: getComputedStyle).

function shownWithin(el, stopAt, getStyle) {
    for (let n = el; n && n !== stopAt; n = n.parentElement) {
        if (n.hidden || getStyle(n).display === 'none') return false;
    }
    return true;
}

export function topmostDialog(root = document, getStyle = (el) => getComputedStyle(el)) {
    let top = null;
    let topZ = -Infinity;
    for (const el of root.querySelectorAll(MODAL_SEL)) {
        if (!shownWithin(el, null, getStyle)) continue;
        const z = parseInt(getStyle(el).zIndex, 10);
        const zi = Number.isNaN(z) ? 0 : z;
        // Ties go to the later element in DOM order (opened over the earlier).
        if (zi >= topZ) { top = el; topZ = zi; }
    }
    return top;
}

// Returns the dialog it closed, or null if none was closed (no dialog open, or
// the topmost one is a required gate marked data-modal-persistent).
export function closeTopmostDialog(root = document, getStyle = (el) => getComputedStyle(el)) {
    const top = topmostDialog(root, getStyle);
    if (!top || top.hasAttribute('data-modal-persistent')) return null;
    const cancel = Array.from(top.querySelectorAll('[data-modal-cancel]'))
        .find((b) => shownWithin(b, top, getStyle));
    if (cancel) cancel.click();
    else closeDialog(top);
    return top;
}
