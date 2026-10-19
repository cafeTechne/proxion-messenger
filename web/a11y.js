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

// Controls that can sit inside a row.
const INNER_CONTROLS = 'button, a[href], input, select, textarea, audio[controls], video[controls], summary, [tabindex]';
// Targets that keep their own arrow / Home / End behaviour.
const NATIVE_KEYS = 'input, textarea, select, audio, video';

export function makeListNavigable(listEl, { onActivate, onDelete, onContextMenu, itemSelector, innerTabbable, innerSkip } = {}) {
    if (!listEl || listEl._a11yNav) return;
    listEl._a11yNav = true;

    // Empty-state rows (.sidebar-empty) are not conversations: they hold a CTA
    // button ("Create a room") that must stay in the normal tab order.
    const isEmptyRow = (el) => !!el.classList?.contains('sidebar-empty');
    const items = () => (itemSelector
        ? Array.from(listEl.querySelectorAll(itemSelector))
        : Array.from(listEl.children).filter(el => el.tagName === 'LI'))
        .filter(el => !isEmptyRow(el));
    const itemOf = (target) => items().find(el => el === target || el.contains(target)) || null;
    // The element that takes focus for a row: a child marked
    // data-roving-target (the named role=button body of a row that also holds
    // its own buttons) or the row itself. Focusing a bare <li> left screen
    // readers on a nameless "listitem".
    const focusEl = (it) => it.querySelector?.('[data-roving-target]') || it;
    function setStop(it, on) {
        const f = focusEl(it);
        f.tabIndex = on ? 0 : -1;
        if (f !== it) it.removeAttribute?.('tabindex');
    }

    // With innerTabbable (the message feed), the controls inside the CURRENT
    // row stay in the tab order: links, the image button, poll options,
    // downloads, spoilers and reactions are content, not row chrome, and
    // keyboard users must reach them. Tab goes row -> its controls -> out of
    // the list; other rows' controls stay out so the list is still one stop.
    // A control nested in another interactive element (the image button under
    // a spoiler cover) stays out until the cover is gone, and innerSkip names
    // chrome reachable another way (the hover action bar, via the row menu).
    function innerAllowed(row, ctl) {
        if (!innerTabbable) return false;
        if (innerSkip && ctl.closest?.(innerSkip)) return false;
        const parent = ctl.parentElement?.closest?.('[role="button"][tabindex], button, a[href]');
        return !(parent && parent !== row && row.contains(parent));
    }
    function applyInner(row, current) {
        const f = focusEl(row);
        row.querySelectorAll(INNER_CONTROLS).forEach(b => {
            if (b === row || b === f) return;
            b.tabIndex = current && innerAllowed(row, b) ? 0 : -1;
        });
    }

    // Exactly one item is a tab stop (the active row if present, else the first).
    // Nested buttons become tabindex=-1 so the list is a single tab stop; they're
    // reachable via Delete (onDelete) instead.
    function setRoving() {
        const its = items();
        if (!its.length) return;
        let idx = its.findIndex(el => focusEl(el).tabIndex === 0);
        if (idx < 0) idx = its.findIndex(el => el.classList.contains('active'));
        if (idx < 0) idx = 0;
        its.forEach((el, i) => {
            setStop(el, i === idx);
            applyInner(el, i === idx);
        });
    }

    function setCurrent(i, focus) {
        const its = items();
        if (!its.length) return;
        const c = Math.max(0, Math.min(i, its.length - 1));
        its.forEach((el, j) => {
            const was = focusEl(el).tabIndex === 0;
            setStop(el, j === c);
            if (innerTabbable && (was || j === c)) applyInner(el, j === c);
        });
        if (focus) focusEl(its[c]).focus();
    }
    const focusItem = (i) => setCurrent(i, true);

    // Focus that reaches a row some other way (a click, a link inside it)
    // makes that row the current one, so Tab continues from where the user is.
    listEl.addEventListener('focusin', (e) => {
        const item = itemOf(e.target);
        if (!item || focusEl(item).tabIndex === 0) return;
        setCurrent(items().indexOf(item), false);
    });

    listEl.addEventListener('keydown', (e) => {
        const item = itemOf(e.target);
        const its = items();
        const cur = its.indexOf(item);
        if (cur < 0) return;
        // Shift+F10 / the ContextMenu key open the per-item actions menu.
        if ((e.key === 'F10' && e.shiftKey) || e.key === 'ContextMenu') {
            if (onContextMenu) { e.preventDefault(); onContextMenu(its[cur]); return; }
        }
        const onInner = e.target !== its[cur] && e.target !== focusEl(its[cur]);
        // A media player or field inside the row keeps its own arrow keys.
        if (onInner && e.target.closest?.(NATIVE_KEYS)) return;
        switch (e.key) {
            case 'ArrowDown': e.preventDefault(); focusItem(cur + 1); break;
            case 'ArrowUp': e.preventDefault(); focusItem(cur - 1); break;
            case 'Home': e.preventDefault(); focusItem(0); break;
            case 'End': e.preventDefault(); focusItem(its.length - 1); break;
            case 'Enter':
            case ' ': {
                // A real control inside the row (image button, GIF play/pause,
                // spoiler, edited badge) handles its own activation.
                // A row whose own handler already ran (preventDefault) is
                // not opened a second time.
                if (e.defaultPrevented) return;
                const ctl = onInner ? e.target.closest?.('button, a[href], input, textarea, select, [role="button"], [tabindex]') : null;
                if (ctl && ctl !== its[cur]) return;
                e.preventDefault();
                if (onActivate) onActivate(its[cur]); else its[cur].click();
                break;
            }
            case 'Delete':
            case 'Backspace':
                if (onDelete && !onInner) { e.preventDefault(); onDelete(its[cur]); }
                break;
            default: break;
        }
    });

    // The sidebar lists are rebuilt wholesale (innerHTML = "" then re-append),
    // which wipes tabindex — re-assert it after every mutation. The feed also
    // watches the subtree: reactions and receipts render into rows after they
    // are inserted. Bursts of mutations collapse into one pass.
    let queued = false;
    const obs = new MutationObserver(() => {
        if (!innerTabbable) { setRoving(); return; }
        if (queued) return;
        queued = true;
        queueMicrotask(() => { queued = false; setRoving(); });
    });
    obs.observe(listEl, { childList: true, subtree: !!innerTabbable });
    setRoving();
}

// ── Live-region announcements ────────────────────────────────────────────────

let _politeRegion = null;
let _assertiveRegion = null;

function _makeRegion(role, live) {
    const el = document.createElement('div');
    // The vitest env stubs createElement with a minimal object — bail if it
    // isn't a real element so announce() is a harmless no-op under test.
    if (!el || typeof el.setAttribute !== 'function') return null;
    el.setAttribute('role', role);
    el.setAttribute('aria-live', live);
    el.className = 'sr-only';
    document.body.appendChild(el);
    return el;
}

function ensureRegions() {
    if (typeof document === 'undefined' || typeof document.createElement !== 'function') return;
    if (!_politeRegion) _politeRegion = _makeRegion('status', 'polite');
    if (!_assertiveRegion) _assertiveRegion = _makeRegion('alert', 'assertive');
}

export function announce(message, assertive = false) {
    if (!message) return;
    ensureRegions();
    const region = assertive ? _assertiveRegion : _politeRegion;
    if (!region) return;
    // Clear then set on the next frame so identical consecutive messages are
    // re-announced (a live region that receives the same text twice is silent).
    region.textContent = '';
    const raf = (typeof requestAnimationFrame !== 'undefined')
        ? requestAnimationFrame : ((cb) => setTimeout(cb, 0));
    raf(() => { region.textContent = String(message); });
}
