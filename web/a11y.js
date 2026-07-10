// a11y.js — keyboard-navigation + live-region helpers (PLAN_ROUND_56 B2/C).
//
// Self-contained factory-free module. Two exports:
//   makeListNavigable(listEl, opts) — roving-tabindex keyboard nav for a list
//     of clickable rows (the sidebar conversation lists are <li>s with click
//     handlers but no tabindex, so keyboard users can't open a conversation at
//     all — a hard WCAG 2.1.1 failure axe can't see). One tab stop per list;
//     Up/Down move, Home/End jump, Enter/Space activate, optional Delete.
//   announce(msg, assertive?) — push a message to the visually-hidden live
//     region so a screen reader speaks it (toasts, connection changes, …).

// ── Roving tabindex ─────────────────────────────────────────────────────────

export function makeListNavigable(listEl, { onActivate, onDelete, onContextMenu, itemSelector } = {}) {
    if (!listEl || listEl._a11yNav) return;
    listEl._a11yNav = true;

    const items = () => itemSelector
        ? Array.from(listEl.querySelectorAll(itemSelector))
        : Array.from(listEl.children).filter(el => el.tagName === 'LI');
    const itemOf = (target) => items().find(el => el === target || el.contains(target)) || null;

    // Exactly one item is a tab stop (the active row if present, else the first).
    // Nested buttons become tabindex=-1 so the list is a single tab stop; they're
    // reachable via Delete (onDelete) instead.
    function setRoving() {
        const its = items();
        if (!its.length) return;
        let idx = its.findIndex(el => el.tabIndex === 0);
        if (idx < 0) idx = its.findIndex(el => el.classList.contains('active'));
        if (idx < 0) idx = 0;
        its.forEach((el, i) => {
            el.tabIndex = i === idx ? 0 : -1;
            el.querySelectorAll('button, a[href], [tabindex]').forEach(b => {
                if (b !== el) b.tabIndex = -1;
            });
        });
    }

    function focusItem(i) {
        const its = items();
        if (!its.length) return;
        const c = Math.max(0, Math.min(i, its.length - 1));
        its.forEach((el, j) => { el.tabIndex = j === c ? 0 : -1; });
        its[c].focus();
    }

    listEl.addEventListener('keydown', (e) => {
        const item = itemOf(e.target);
        const its = items();
        const cur = its.indexOf(item);
        if (cur < 0) return;
        // Shift+F10 / the ContextMenu key open the per-item actions menu.
        if ((e.key === 'F10' && e.shiftKey) || e.key === 'ContextMenu') {
            if (onContextMenu) { e.preventDefault(); onContextMenu(its[cur]); return; }
        }
        switch (e.key) {
            case 'ArrowDown': e.preventDefault(); focusItem(cur + 1); break;
            case 'ArrowUp': e.preventDefault(); focusItem(cur - 1); break;
            case 'Home': e.preventDefault(); focusItem(0); break;
            case 'End': e.preventDefault(); focusItem(its.length - 1); break;
            case 'Enter':
            case ' ':
                e.preventDefault();
                if (onActivate) onActivate(its[cur]); else its[cur].click();
                break;
            case 'Delete':
            case 'Backspace':
                if (onDelete) { e.preventDefault(); onDelete(its[cur]); }
                break;
            default: break;
        }
    });

    // The sidebar lists are rebuilt wholesale (innerHTML = "" then re-append),
    // which wipes tabindex — re-assert it after every mutation.
    const obs = new MutationObserver(() => setRoving());
    obs.observe(listEl, { childList: true });
    setRoving();
}

// ── Live-region announcements ────────────────────────────────────────────────

let _politeRegion = null;
let _assertiveRegion = null;

function ensureRegions() {
    if (typeof document === 'undefined') return;
    if (!_politeRegion) {
        _politeRegion = document.createElement('div');
        _politeRegion.setAttribute('role', 'status');
        _politeRegion.setAttribute('aria-live', 'polite');
        _politeRegion.className = 'sr-only';
        document.body.appendChild(_politeRegion);
    }
    if (!_assertiveRegion) {
        _assertiveRegion = document.createElement('div');
        _assertiveRegion.setAttribute('role', 'alert');
        _assertiveRegion.setAttribute('aria-live', 'assertive');
        _assertiveRegion.className = 'sr-only';
        document.body.appendChild(_assertiveRegion);
    }
}

export function announce(message, assertive = false) {
    if (!message) return;
    ensureRegions();
    const region = assertive ? _assertiveRegion : _politeRegion;
    if (!region) return;
    // Clear then set on the next frame so identical consecutive messages are
    // re-announced (a live region that receives the same text twice is silent).
    region.textContent = '';
    requestAnimationFrame(() => { region.textContent = String(message); });
}
