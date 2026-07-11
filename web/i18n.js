// i18n.js — internationalization core (PLAN_ROUND_56 F1).
//
// Vanilla, dependency-free, factory-free (like a11y.js). Loads a flat dot-key
// locale JSON from /locales/<code>.json (same-origin static file the gateway
// already serves), with per-key fallback to English.
//
// Exports:
//   initI18n()               resolve+load the active locale; call once at boot
//   t(key, params?)          plain-text lookup with {name} interpolation
//   tn(key, count, params?)  Intl.PluralRules plural selection (key.one/.other)
//   applyStaticI18n(root?)   translate [data-i18n] / [data-i18n-attr] in the DOM
//   getLocale() / isRTL()    active locale + direction
//   setLocale(code)          persist + reload (honest, cheap re-render)
//   onLocaleChange(cb)       subscribe (for a future live re-render)
//
// Escaping contract: t()/tn() return PLAIN TEXT. Callers keep escaping at the
// HTML sink exactly as before (escHtml). Locale VALUES must never contain markup
// (enforced by scripts/i18n_check.mjs) so a translated string can't open an
// injection path.

// Shippable locales (qps is a test-only generated pseudo-locale, never listed
// here so it's not auto-selected from navigator.languages). Each carries its
// endonym (name in its own language) and a draft flag for the picker.
export const LOCALE_META = {
    en: { name: 'English', draft: false },
    es: { name: 'Español', draft: true },
    de: { name: 'Deutsch', draft: true },
    fr: { name: 'Français', draft: true },
    ar: { name: 'العربية', draft: true },
};
export const SUPPORTED_LOCALES = Object.keys(LOCALE_META);
const RTL_LANGS = ['ar', 'he', 'fa', 'ur'];

let _locale = 'en';
let _messages = {};     // active-locale flat map
let _fallback = {};     // en flat map (always loaded)
let _plural = null;
const _listeners = [];

function _navLocales() {
    if (typeof navigator === 'undefined') return ['en'];
    return navigator.languages && navigator.languages.length
        ? navigator.languages : [navigator.language || 'en'];
}

// localStorage override → navigator best-match (exact then base) → en. A test-
// only pseudo-locale (qps / qps-ploc) is honored ONLY via the explicit override.
function _resolveLocale() {
    try {
        const saved = localStorage.getItem('proxion_locale');
        if (saved) return saved.toLowerCase();
    } catch { /* private mode */ }
    for (const cand of _navLocales()) {
        const lc = String(cand).toLowerCase();
        if (SUPPORTED_LOCALES.includes(lc)) return lc;
        const base = lc.split('-')[0];
        if (SUPPORTED_LOCALES.includes(base)) return base;
    }
    return 'en';
}

async function _fetchLocale(code) {
    if (typeof fetch !== 'function') return {};
    try {
        const res = await fetch(`/locales/${encodeURIComponent(code)}.json`, { cache: 'no-store' });
        if (!res.ok) return {};
        const data = await res.json();
        if (data && typeof data === 'object') { delete data._meta; return data; }
        return {};
    } catch { return {}; }
}

function _safePluralRules(code) {
    try { return new Intl.PluralRules(code); } catch { return new Intl.PluralRules('en'); }
}

export async function initI18n() {
    _locale = _resolveLocale();
    _fallback = await _fetchLocale('en');
    _messages = _locale === 'en' ? _fallback : await _fetchLocale(_locale);
    _plural = _safePluralRules(_locale);
    _mirrorForServiceWorker();
    return _locale;
}

export function getLocale() { return _locale; }
export function isRTL() { return RTL_LANGS.includes(_locale.split('-')[0]); }

function _lookup(key) {
    if (Object.prototype.hasOwnProperty.call(_messages, key)) return _messages[key];
    if (Object.prototype.hasOwnProperty.call(_fallback, key)) return _fallback[key];
    return null;
}

function _interp(str, params) {
    if (!params) return str;
    return str.replace(/\{(\w+)\}/g, (m, k) =>
        (Object.prototype.hasOwnProperty.call(params, k) ? String(params[k]) : m));
}

export function t(key, params) {
    const v = _lookup(key);
    // Missing key → return the key itself: visible in the UI and greppable, and
    // never throws mid-render.
    if (v == null) return key;
    return _interp(v, params);
}

export function tn(key, count, params) {
    const n = Number(count) || 0;
    const cat = (_plural || _safePluralRules('en')).select(n);
    const v = _lookup(`${key}.${cat}`) ?? _lookup(`${key}.other`);
    if (v == null) return key;
    return _interp(v, { ...(params || {}), count: n });
}

// Walk the static DOM: text nodes tagged data-i18n, attributes tagged
// data-i18n-attr="title:key;placeholder:key2". English stays in index.html as
// fallback content, so a fetch failure (or `en`) renders unchanged.
export function applyStaticI18n(root) {
    if (typeof document === 'undefined') return;
    const scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach((el) => {
        const v = _lookup(el.getAttribute('data-i18n'));
        if (v != null) el.textContent = v;
    });
    scope.querySelectorAll('[data-i18n-attr]').forEach((el) => {
        el.getAttribute('data-i18n-attr').split(';').forEach((pair) => {
            const idx = pair.indexOf(':');
            if (idx < 0) return;
            const attr = pair.slice(0, idx).trim();
            const key = pair.slice(idx + 1).trim();
            if (!attr || !key) return;
            const v = _lookup(key);
            if (v != null) el.setAttribute(attr, v);
        });
    });
    try {
        document.documentElement.lang = _locale;
        document.documentElement.dir = isRTL() ? 'rtl' : 'ltr';
    } catch { /* no documentElement in tests */ }
}

export function onLocaleChange(cb) { if (typeof cb === 'function') _listeners.push(cb); }

export function setLocale(code) {
    try { localStorage.setItem('proxion_locale', String(code)); } catch { /* private mode */ }
    _listeners.forEach((cb) => { try { cb(code); } catch { /* ignore */ } });
    // Simplest correct re-render: reload with the new locale persisted. A live
    // re-render without reload is a deliberate non-goal (see PLAN_ROUND_56 F1).
    try { location.reload(); } catch { /* non-browser */ }
}

// The service worker can't import this module — and can't read localStorage
// (unavailable in the SW scope) — so mirror the locale + the tiny push string
// set into IndexedDB, which the SW push handler CAN read at notify time (G4).
// A localStorage copy is kept too for any same-context reader.
function _mirrorForServiceWorker() {
    const payload = {
        locale: _locale,
        newMessage: _lookup('push.newMessage') || 'New message',
        newMessageFrom: _lookup('push.newMessageFrom') || 'New message from {name}',
    };
    try { localStorage.setItem('proxion_i18n_push', JSON.stringify(payload)); } catch { /* ignore */ }
    try {
        if (typeof indexedDB === 'undefined') return;
        const req = indexedDB.open('proxion-i18n', 1);
        req.onupgradeneeded = () => { try { req.result.createObjectStore('kv'); } catch { /* exists */ } };
        req.onsuccess = () => {
            try {
                const tx = req.result.transaction('kv', 'readwrite');
                tx.objectStore('kv').put(payload, 'push');
            } catch { /* ignore */ }
        };
    } catch { /* ignore */ }
}
