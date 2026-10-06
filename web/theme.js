// Theme preference: System (follow prefers-color-scheme), Dark or Light.
// Stored per device in localStorage and applied as data-theme on <html>.
// "system" removes the attribute and lets the CSS media query decide, so the
// page follows the OS live without any listener here.
//
// theme-boot.js applies the stored value before first paint (it is a classic
// script in <head>); this module owns the Settings picker. Both read the same
// key; theme.test.js runs them against the same cases.

export const THEME_KEY = 'proxion_theme';
export const THEMES = ['system', 'dark', 'light'];

function defaultStorage() {
    try { return globalThis.localStorage; } catch (_) { return undefined; }
}

/** The saved preference, or "system" when unset, invalid or unreadable. */
export function readThemePref(storage = defaultStorage()) {
    try {
        const v = storage ? storage.getItem(THEME_KEY) : null;
        return THEMES.includes(v) ? v : 'system';
    } catch (_) {
        return 'system';
    }
}

/** Save a preference. Returns false when storage refuses (private mode,
 *  blocked site data); the caller still applies it for this session. */
export function writeThemePref(pref, storage = defaultStorage()) {
    const v = THEMES.includes(pref) ? pref : 'system';
    try {
        if (!storage) return false;
        if (v === 'system') storage.removeItem(THEME_KEY);
        else storage.setItem(THEME_KEY, v);
        return true;
    } catch (_) {
        return false;
    }
}

/** Set or clear data-theme on the root element, and keep the theme-color
 *  metas in step with it. */
export function applyTheme(pref, root = globalThis.document && globalThis.document.documentElement) {
    if (!root) return;
    if (pref === 'dark' || pref === 'light') root.setAttribute('data-theme', pref);
    else root.removeAttribute('data-theme');
    syncThemeColor(pref, root.ownerDocument || globalThis.document);
}

/** index.html carries one theme-color meta per scheme (media query), each
 *  remembering its own colour in data-color. A pinned theme copies its colour
 *  into both, so the toolbar matches whatever the OS prefers; "system" puts
 *  each back. theme-boot.js does the same before first paint. */
export function syncThemeColor(pref, doc) {
    if (!doc || typeof doc.querySelectorAll !== 'function') return;
    const metas = Array.from(doc.querySelectorAll('meta[name="theme-color"][data-color]'));
    const pinned = metas.find((m) => m.getAttribute('data-scheme') === pref);
    for (const m of metas) m.setAttribute('content', (pinned || m).getAttribute('data-color'));
}

/** The theme actually showing: the pinned one, or the OS preference. */
export function effectiveTheme(pref, matchMedia = globalThis.matchMedia) {
    if (pref === 'dark' || pref === 'light') return pref;
    try {
        return matchMedia && matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    } catch (_) {
        return 'dark';
    }
}

/** Wire the Settings <select> (options system/dark/light live in index.html). */
export function initThemePicker(select, { storage = defaultStorage(), root } = {}) {
    if (!select) return;
    select.value = readThemePref(storage);
    select.addEventListener('change', () => {
        const pref = THEMES.includes(select.value) ? select.value : 'system';
        writeThemePref(pref, storage);
        applyTheme(pref, root);
    });
}
