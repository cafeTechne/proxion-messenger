import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import {
    THEME_KEY, readThemePref, writeThemePref, applyTheme, effectiveTheme, initThemePicker,
} from './theme.js';

const HERE = dirname(fileURLToPath(import.meta.url));

function memStorage(init = {}) {
    const m = new Map(Object.entries(init));
    return {
        getItem: (k) => (m.has(k) ? m.get(k) : null),
        setItem: (k, v) => { m.set(k, String(v)); },
        removeItem: (k) => { m.delete(k); },
        _m: m,
    };
}
const throwing = {
    getItem() { throw new Error('SecurityError'); },
    setItem() { throw new Error('QuotaExceededError'); },
    removeItem() { throw new Error('SecurityError'); },
};
function fakeRoot() {
    const attrs = new Map();
    return {
        setAttribute: (k, v) => attrs.set(k, String(v)),
        removeAttribute: (k) => attrs.delete(k),
        getAttribute: (k) => (attrs.has(k) ? attrs.get(k) : null),
    };
}
const mm = (light) => (q) => ({ matches: q === '(prefers-color-scheme: light)' ? light : false });

describe('readThemePref', () => {
    it('defaults to system when nothing is stored', () => {
        expect(readThemePref(memStorage())).toBe('system');
    });
    it('returns a stored dark or light choice', () => {
        expect(readThemePref(memStorage({ [THEME_KEY]: 'dark' }))).toBe('dark');
        expect(readThemePref(memStorage({ [THEME_KEY]: 'light' }))).toBe('light');
    });
    it('treats an unknown stored value as system', () => {
        expect(readThemePref(memStorage({ [THEME_KEY]: 'sepia' }))).toBe('system');
    });
    it('falls back to system when storage throws or is missing', () => {
        expect(readThemePref(throwing)).toBe('system');
        expect(readThemePref(undefined)).toBe('system');
    });
});

describe('writeThemePref', () => {
    it('stores dark and light, and clears the key for system', () => {
        const s = memStorage();
        expect(writeThemePref('light', s)).toBe(true);
        expect(s.getItem(THEME_KEY)).toBe('light');
        expect(writeThemePref('system', s)).toBe(true);
        expect(s._m.has(THEME_KEY)).toBe(false);
    });
    it('coerces an invalid value to system', () => {
        const s = memStorage({ [THEME_KEY]: 'dark' });
        writeThemePref('neon', s);
        expect(s._m.has(THEME_KEY)).toBe(false);
    });
    it('reports failure instead of throwing when storage refuses', () => {
        expect(writeThemePref('dark', throwing)).toBe(false);
        expect(writeThemePref('dark', undefined)).toBe(false);
    });
});

describe('applyTheme and effectiveTheme', () => {
    it('sets data-theme for dark and light, and removes it for system', () => {
        const root = fakeRoot();
        applyTheme('light', root);
        expect(root.getAttribute('data-theme')).toBe('light');
        applyTheme('dark', root);
        expect(root.getAttribute('data-theme')).toBe('dark');
        applyTheme('system', root);
        expect(root.getAttribute('data-theme')).toBe(null);
    });
    it('system follows the OS, a pinned theme ignores it', () => {
        expect(effectiveTheme('system', mm(true))).toBe('light');
        expect(effectiveTheme('system', mm(false))).toBe('dark');
        expect(effectiveTheme('dark', mm(true))).toBe('dark');
        expect(effectiveTheme('light', mm(false))).toBe('light');
    });
    it('system is dark when matchMedia is unavailable', () => {
        expect(effectiveTheme('system', undefined)).toBe('dark');
        expect(effectiveTheme('system', () => { throw new Error('nope'); })).toBe('dark');
    });
});

describe('initThemePicker', () => {
    function fakeSelect() {
        const handlers = {};
        return {
            value: '',
            addEventListener: (ev, fn) => { handlers[ev] = fn; },
            fire(v) { this.value = v; handlers.change(); },
        };
    }
    it('shows the stored choice and saves and applies a change', () => {
        const s = memStorage({ [THEME_KEY]: 'dark' });
        const root = fakeRoot();
        const sel = fakeSelect();
        initThemePicker(sel, { storage: s, root });
        expect(sel.value).toBe('dark');
        sel.fire('light');
        expect(s.getItem(THEME_KEY)).toBe('light');
        expect(root.getAttribute('data-theme')).toBe('light');
        sel.fire('system');
        expect(s._m.has(THEME_KEY)).toBe(false);
        expect(root.getAttribute('data-theme')).toBe(null);
    });
    it('still applies the theme for this session when storage fails', () => {
        const root = fakeRoot();
        const sel = fakeSelect();
        initThemePicker(sel, { storage: throwing, root });
        expect(sel.value).toBe('system');
        sel.fire('light');
        expect(root.getAttribute('data-theme')).toBe('light');
    });
});

describe('theme-boot.js (pre-paint)', () => {
    const src = readFileSync(join(HERE, 'theme-boot.js'), 'utf8');
    function boot(storage) {
        const root = fakeRoot();
        const window = {};
        Object.defineProperty(window, 'localStorage', {
            get() { if (storage === 'deny') throw new Error('SecurityError'); return storage; },
        });
        vm.runInNewContext(src, { window, document: { documentElement: root } });
        return root.getAttribute('data-theme');
    }
    it('agrees with readThemePref + applyTheme', () => {
        for (const v of [null, 'dark', 'light', 'system', 'bogus']) {
            const s = memStorage(v === null ? {} : { [THEME_KEY]: v });
            const root = fakeRoot();
            applyTheme(readThemePref(s), root);
            expect(boot(s)).toBe(root.getAttribute('data-theme'));
        }
    });
    it('does not throw when localStorage access is denied', () => {
        expect(boot('deny')).toBe(null);
        expect(boot(throwing)).toBe(null);
    });
});
