// menu.js: shared keyboard and positioning behaviour for the popup menus
// (#ctx-menu, #sidebar-ctx-menu, #delete-submenu, #member-context-menu).
//
// Roles are applied at open time, so buttons added to a menu's markup later
// are covered without touching this file:
//   container role="menu", each <button> role="menuitem", <hr> role="separator".
// While open: ArrowUp/ArrowDown wrap, Home/End jump, Escape and Tab close and
// return focus to the opener, and focus leaving the menu closes it.

// ---- Pure helpers (unit-tested) ----

// Clamp a menu of size w x h, requested at (x, y), into a vw x vh viewport.
export function clampMenuPosition(x, y, w, h, vw, vh, margin = 8) {
    const left = Math.max(margin, Math.min(x, vw - w - margin));
    const top = Math.max(margin, Math.min(y, vh - h - margin));
    return { left, top };
}

// What a key press does inside an open menu with `count` items, focus at `cur`
// (-1 when no item has focus). Returns { focus: index }, { close: true }, or null.
export function menuKeyAction(key, cur, count) {
    if (!count) return key === 'Escape' || key === 'Tab' ? { close: true } : null;
    switch (key) {
        case 'ArrowDown': return { focus: cur < 0 ? 0 : (cur + 1) % count };
        case 'ArrowUp': return { focus: cur < 0 ? count - 1 : (cur - 1 + count) % count };
        case 'Home': return { focus: 0 };
        case 'End': return { focus: count - 1 };
        case 'Escape':
        case 'Tab': return { close: true };
        default: return null;
    }
}

// ---- DOM wiring ----

function isShownItem(b) {
    return !b.disabled && !b.hidden && b.style.display !== 'none';
}

export function menuItems(menu) {
    return Array.from(menu.querySelectorAll('button')).filter(isShownItem);
}

function applyRoles(menu) {
    menu.setAttribute('role', 'menu');
    menu.querySelectorAll('button').forEach((b) => {
        b.setAttribute('role', 'menuitem');
        b.tabIndex = -1;
    });
    menu.querySelectorAll('hr').forEach((h) => h.setAttribute('role', 'separator'));
}

export function isMenuOpen(menu) {
    return !!menu && menu.style.display !== 'none' && menu.style.display !== '';
}

// Close the menu. Focus goes back to the opener when the menu held focus
// (keyboard use, or an item was activated); a click elsewhere keeps focus where
// the user put it. Returns true if the menu was open.
export function closeMenu(menu, { restoreFocus } = {}) {
    if (!isMenuOpen(menu)) return false;
    const doc = menu.ownerDocument || document;
    const hadFocus = menu.contains(doc.activeElement);
    menu.style.display = 'none';
    const opener = menu._menuOpener;
    const onClose = menu._menuOnClose;
    menu._menuOpener = null;
    menu._menuOnClose = null;
    if (onClose) onClose();
    const restore = restoreFocus === undefined ? hadFocus : restoreFocus;
    if (restore && opener && doc.contains(opener) && typeof opener.focus === 'function') {
        try { opener.focus(); } catch { /* gone */ }
    }
    return true;
}

function wireMenu(menu) {
    if (menu._menuWired) return;
    menu._menuWired = true;
    menu.addEventListener('keydown', (e) => {
        const items = menuItems(menu);
        const doc = menu.ownerDocument || document;
        const act = menuKeyAction(e.key, items.indexOf(doc.activeElement), items.length);
        if (!act) return;
        e.preventDefault();
        e.stopPropagation();
        if (act.close) closeMenu(menu, { restoreFocus: true });
        else items[act.focus].focus();
    });
    menu.addEventListener('focusout', (e) => {
        const to = e.relatedTarget;
        if (to && !menu.contains(to)) closeMenu(menu, { restoreFocus: false });
    });
}

// Re-apply roles and focus the first item, for menus whose body is swapped
// while open (the member menu's mute-duration step).
export function refreshMenu(menu, { focus = true } = {}) {
    applyRoles(menu);
    if (focus) {
        const first = menuItems(menu)[0];
        if (first) first.focus();
    }
}

// Show `menu` at (x, y), clamped to the viewport after measuring its real
// size. `opener` gets focus back on Escape/Tab; `onClose` runs on any close.
export function openMenu(menu, { x, y, opener = null, onClose = null, focus = true } = {}) {
    if (!menu) return;
    wireMenu(menu);
    applyRoles(menu);
    menu._menuOpener = opener;
    menu._menuOnClose = onClose;
    menu.style.display = 'block';
    if (typeof x === 'number' && typeof y === 'number') {
        const r = menu.getBoundingClientRect();
        const { left, top } = clampMenuPosition(x, y, r.width, r.height,
            window.innerWidth, window.innerHeight);
        menu.style.left = left + 'px';
        menu.style.top = top + 'px';
    }
    if (focus) {
        const raf = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (cb) => setTimeout(cb, 0);
        raf(() => {
            if (!isMenuOpen(menu)) return;
            const first = menuItems(menu)[0];
            if (first) first.focus();
        });
    }
}
