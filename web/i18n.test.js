import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { initI18n } from './i18n.js';

describe('locale fetch URL', () => {
    let calls;
    beforeEach(() => {
        calls = [];
        globalThis.fetch = vi.fn(async (url) => {
            calls.push(String(url));
            return { ok: true, json: async () => ({ 'app.title': 'Proxion' }) };
        });
    });
    afterEach(() => { delete globalThis.fetch; });

    it('requests the locale resolved against the module base, not root-absolute', async () => {
        await initI18n();
        expect(calls.length).toBeGreaterThan(0);
        const url = calls[0];
        // Base-resolved: an absolute URL (file:// under the node test env) whose
        // path ends with locales/en.json. A bare root-absolute /locales/en.json
        // would 404 under a sub-path deploy like /proxion-messenger/app/.
        expect(url.endsWith('locales/en.json')).toBe(true);
        expect(url.startsWith('/locales/')).toBe(false);
        expect(url).toMatch(/^[a-z]+:\/\//);
    });
});

describe('locale URL resolution semantics', () => {
    // The fix relies on `new URL('locales/<code>.json', import.meta.url)`. Assert
    // the resolution against both deployment bases the app actually runs under.
    it('resolves next to the app under a Pages sub-path deploy', () => {
        const base = 'https://cafetechne.github.io/proxion-messenger/app/i18n.js';
        expect(new URL('locales/en.json', base).href)
            .toBe('https://cafetechne.github.io/proxion-messenger/app/locales/en.json');
    });

    it('resolves at the domain root when served from /', () => {
        const base = 'https://gateway.example/i18n.js';
        expect(new URL('locales/en.json', base).href)
            .toBe('https://gateway.example/locales/en.json');
    });
});
