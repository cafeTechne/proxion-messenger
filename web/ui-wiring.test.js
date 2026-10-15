import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

// Static wiring guards. A lookup of an id that no longer exists returns null,
// and the usual `?.` / `if (el)` guard then turns a renamed element into a
// button that silently does nothing (the add-contact deep link wrote into a
// "#peer-address" input that had been renamed, so "Add contact" did nothing).

const WEB = fileURLToPath(new URL('.', import.meta.url));
const CORE = join(WEB, '..', 'proxion-messenger-core', 'src', 'proxion_messenger_core');
const read = (p) => readFileSync(p, 'utf8');
const sources = Object.fromEntries(readdirSync(WEB)
    .filter(f => f.endsWith('.js') && !f.includes('.test.') && !f.startsWith('smoke')
        && f !== 'solid-authn.bundle.js' && f !== 'sw.js')
    .map(f => [f, read(join(WEB, f))]));
const html = read(join(WEB, 'index.html'));

// Lookups that are knowingly optional (a fallback for an element that may be
// absent). Keep this list short: a new entry usually means a stale id.
const OPTIONAL_IDS = new Set(['chat-input', 'room-list-empty-hint']);

describe('UI wiring', () => {
    it('every literal getElementById / #id lookup names an element that exists', () => {
        const defined = new Set();
        for (const m of html.matchAll(/\sid=["']([^"']+)["']/g)) defined.add(m[1]);
        for (const s of Object.values(sources)) {
            for (const m of s.matchAll(/\bid=\\?["']([^"'${}\\]+)\\?["']/g)) defined.add(m[1]);
            for (const m of s.matchAll(/\.id\s*=\s*[`'"]([^`'"${}]+)[`'"]/g)) defined.add(m[1]);
            for (const m of s.matchAll(/\bid:\s*['"]([a-z][\w-]+)['"]/g)) defined.add(m[1]);
        }
        const missing = [];
        for (const [f, s] of Object.entries(sources)) {
            s.split('\n').forEach((line, i) => {
                const refs = [
                    ...line.matchAll(/getElementById\(\s*['"`]([^'"`$]+)['"`]\s*\)/g),
                    ...line.matchAll(/querySelector(?:All)?\(\s*['"`]#([\w-]+)/g),
                ];
                for (const m of refs) {
                    if (!defined.has(m[1]) && !OPTIONAL_IDS.has(m[1])) missing.push(`${f}:${i + 1} #${m[1]}`);
                }
            });
        }
        expect(missing).toEqual([]);
    });

    it('no inline on<event>= handlers (the gateway CSP blocks them)', () => {
        const inline = /<[a-z][^>]*\son[a-z]+\s*=\s*["']/i;
        const hits = [];
        for (const [f, s] of Object.entries({ ...sources, 'index.html': html })) {
            s.split('\n').forEach((line, i) => { if (inline.test(line)) hits.push(`${f}:${i + 1}`); });
        }
        expect(hits).toEqual([]);
    });

    it('every command the client sends has a gateway route', () => {
        const py = readdirSync(CORE).filter(f => f.endsWith('.py')).map(f => read(join(CORE, f))).join('\n');
        const routes = new Set();
        for (const m of py.matchAll(/cmd\s*==\s*["']([a-z0-9_]+)["']/g)) routes.add(m[1]);
        for (const m of py.matchAll(/cmd\s+in\s+[({[]([^)}\]]+)[)}\]]/g)) {
            for (const n of m[1].matchAll(/["']([a-z0-9_]+)["']/g)) routes.add(n[1]);
        }
        const unrouted = [];
        for (const [f, s] of Object.entries(sources)) {
            for (const m of s.matchAll(/\bcmd\s*:\s*['"]([a-z0-9_]+)['"]/g)) {
                if (!routes.has(m[1])) unrouted.push(`${f}: ${m[1]}`);
            }
        }
        expect(routes.size).toBeGreaterThan(50);
        expect(unrouted).toEqual([]);
    });
});
