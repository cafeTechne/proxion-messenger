// Every script the page loads (index.html <script>s and everything main.js
// imports, transitively) must be in the service worker's SHELL, or the
// offline shell boots into a failed module import.
import { describe, it, expect } from 'vitest';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const here = (p) => fileURLToPath(new URL(`./${p}`, import.meta.url));
const read = (p) => readFileSync(here(p), 'utf8');

function shell() {
    const block = read('sw.js').match(/const SHELL = \[([\s\S]*?)\];/)[1];
    return new Set([...block.matchAll(/"([^"]+)"/g)].map(m => m[1]));
}

function pageScripts() {
    const html = read('index.html');
    const roots = [...html.matchAll(/<script[^>]*\ssrc="([^"]+)"/g)].map(m => m[1]);
    const seen = new Set();
    const walk = (f) => {
        if (seen.has(f) || !existsSync(here(f))) return;
        seen.add(f);
        for (const m of read(f).matchAll(/(?:^|\n)\s*(?:import|export)\s[^'"]*?from\s+['"]\.\/([^'"]+)['"]|import\(\s*['"]\.\/([^'"]+)['"]\s*\)/g)) {
            walk(m[1] || m[2]);
        }
    };
    roots.forEach(walk);
    return [...seen];
}

describe('service worker SHELL', () => {
    it('lists every script the page loads', () => {
        const s = shell();
        const missing = pageScripts().filter(f => !s.has(f));
        expect(missing).toEqual([]);
    });
});
