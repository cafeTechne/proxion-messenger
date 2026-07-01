// focus-trap.js — centralized modal a11y: focus restore + Tab-trapping.
//
// The app has ~20 modals, all toggled by flipping style.display between
// "flex" and "none" (a few, like #integrations-modal, are created on the
// fly). Rather than retrofit every open/close site, we observe the DOM: when
// a dialog becomes visible we (1) remember what had focus, (2) move focus to
// the first focusable control inside it, and (3) trap Tab within it. When it
// hides again we restore focus to wherever it was.
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

let installed = false;

export function installFocusTrap() {
    if (installed || typeof document === 'undefined') return;
    installed = true;

    // The modal currently trapped, plus the element to restore focus to.
    let trapped = null;
    let restoreTo = null;

    function onKeydown(e) {
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
        if (trapped === modal) return;
        // Only remember the opener the first time we start trapping, so that
        // opening a second modal over a first still restores to the original
        // pre-modal focus once everything closes.
        if (!trapped) restoreTo = document.activeElement;
        trapped = modal;
        const items = focusableWithin(modal);
        const target = items[0] || modal;
        // Give a container tabindex so it can hold focus if it has no controls.
        if (target === modal && !modal.hasAttribute('tabindex')) modal.tabIndex = -1;
        // Defer to let any open-time innerHTML settle before focusing.
        requestAnimationFrame(() => { try { target.focus(); } catch { /* detached */ } });
    }

    function release(modal) {
        if (trapped !== modal) return;
        trapped = null;
        const el = restoreTo;
        restoreTo = null;
        // If another modal is still open, trap the topmost one instead of
        // restoring to the page.
        const stillOpen = Array.from(document.querySelectorAll(MODAL_SEL))
            .filter(isVisible);
        if (stillOpen.length) { trap(stillOpen[stillOpen.length - 1]); return; }
        if (el && document.contains(el) && typeof el.focus === 'function') {
            try { el.focus(); } catch { /* gone */ }
        }
    }

    function sync(modal) {
        if (isVisible(modal)) trap(modal);
        else release(modal);
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
                    if (n.nodeType === 1 && n === trapped) release(n);
                });
            }
        }
    });
    observer.observe(document.body, {
        subtree: true, childList: true,
        attributes: true, attributeFilter: ['style', 'class'],
    });

    // Catch any modal already visible at install time.
    document.querySelectorAll(MODAL_SEL).forEach(el => { if (isVisible(el)) trap(el); });
}
