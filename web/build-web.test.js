import { describe, it, expect } from 'vitest';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { injectWebHead, WEB_CSP, FRAME_BUST } from './scripts/build-web.mjs';

describe('injectWebHead', () => {
    it('adds the web-mode meta so detectMode picks web', () => {
        const out = injectWebHead('<head>\n<title>x</title>\n</head>');
        expect(out).toMatch(/<meta name="proxion-mode" content="web">/);
    });

    it('adds a Content-Security-Policy meta', () => {
        const out = injectWebHead('<head></head>');
        expect(out).toContain('http-equiv="Content-Security-Policy"');
        expect(out).toContain(WEB_CSP);
    });

    it('inserts right after <head>, keeping existing head content', () => {
        const out = injectWebHead('<head>\n    <title>Proxion</title>\n</head>');
        expect(out.indexOf('proxion-mode')).toBeLessThan(out.indexOf('<title>'));
        expect(out).toContain('<title>Proxion</title>');
    });

    it('tolerates attributes on the head tag', () => {
        const out = injectWebHead('<head data-x="1">\n</head>');
        expect(out).toMatch(/<head data-x="1">\s*\n\s*<meta name="proxion-mode"/);
    });

    it('is idempotent', () => {
        const once = injectWebHead('<head></head>');
        expect(injectWebHead(once)).toBe(once);
    });

    it('CSP restricts default-src to self and allows pod https/wss', () => {
        expect(WEB_CSP).toContain("default-src 'self'");
        expect(WEB_CSP).toContain('connect-src');
        expect(WEB_CSP).toContain('wss:');
    });

    it('injects the frame-bust guard and whitelists it by hash in the CSP', () => {
        const hash = "'sha256-" + createHash('sha256').update(FRAME_BUST).digest('base64') + "'";
        expect(WEB_CSP).toContain(`script-src 'self' ${hash}`);
        const out = injectWebHead('<head></head>');
        expect(out).toContain(`<script>${FRAME_BUST}</script>`);
    });
});

describe('static build output', () => {
    // The deployed app fetches locales relative to its own base, so the build
    // must ship locales/*.json alongside the JS (they are not in DENY and are not
    // dev-only). Run the real build and confirm the files land, while dev-only
    // files are filtered out.
    it('copies locales/*.json into the build and drops dev-only files', () => {
        const scriptPath = fileURLToPath(new URL('./scripts/build-web.mjs', import.meta.url));
        const root = mkdtempSync(join(tmpdir(), 'proxion-build-'));
        const src = join(root, 'src');
        const out = join(root, 'out');
        try {
            mkdirSync(join(src, 'locales'), { recursive: true });
            writeFileSync(join(src, 'index.html'), '<head></head><body></body>');
            writeFileSync(join(src, 'locales', 'en.json'), '{"app.title":"Proxion"}');
            writeFileSync(join(src, 'x.test.js'), '// dev only');
            execFileSync(process.execPath, [scriptPath, src, out], { stdio: 'pipe' });
            expect(existsSync(join(out, 'locales', 'en.json'))).toBe(true);
            const data = JSON.parse(readFileSync(join(out, 'locales', 'en.json'), 'utf8'));
            expect(data['app.title']).toBe('Proxion');
            expect(existsSync(join(out, 'x.test.js'))).toBe(false);
        } finally {
            rmSync(root, { recursive: true, force: true });
        }
    });
});

describe('frame-bust guard', () => {
    // Run the injected script body with a controlled `window` (it references only
    // window.*, so a Function param shadows the global).
    const run = (win) => new Function('window', FRAME_BUST)(win);

    it('no-ops in a top-level context', () => {
        const style = {};
        const win = { location: { href: 'https://app/', replace() {} }, document: { documentElement: { style } } };
        win.self = win; win.top = win;
        run(win);
        expect(style.display).toBeUndefined();
    });

    it('breaks out of a same-origin frame by navigating top', () => {
        const style = {};
        let navigated = '';
        const win = { location: { href: 'https://app/x' }, document: { documentElement: { style } } };
        win.self = win;
        win.top = { location: { replace: (u) => { navigated = u; } } };
        run(win);
        expect(navigated).toBe('https://app/x');
    });

    it('hides the document when a cross-origin top blocks the break-out', () => {
        const style = {};
        const win = { location: { href: 'https://app/' }, document: { documentElement: { style } } };
        win.self = win;
        win.top = { get location() { throw new Error('cross-origin'); } };
        run(win);
        expect(style.display).toBe('none');
    });
});
