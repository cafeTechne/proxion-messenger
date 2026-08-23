// build-web.mjs — assemble the gateway-less "Proxion Web" static build.
//
// The desktop/self-host build serves web/index.html as-is (gateway mode). The
// Pages build is the same assets with two things injected into <head>:
//   1. <meta name="proxion-mode" content="web">   -> detectMode() picks web mode
//   2. a web Content-Security-Policy meta            -> defense-in-depth for the
//      static origin (talks only to pods over https + wss notifications)
// Injecting at build time keeps the tracked index.html clean, so a gateway that
// serves web/ directly is never accidentally put into web mode.
//
// Usage: node scripts/build-web.mjs <srcDir> <outDir>
// Copies srcDir -> outDir (minus dev-only files) and rewrites index.html.

import { cp, readFile, writeFile, mkdir } from 'node:fs/promises';
import { join } from 'node:path';

// The web CSP for the static build. 'self' for code, https/wss for the user's
// pod and Solid Notifications, data:/https: images for pod avatars, inline
// styles because index.html uses them throughout.
export const WEB_CSP = [
    "default-src 'self'",
    "connect-src 'self' https: wss:",
    "img-src 'self' data: https:",
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self'",
    "font-src 'self' data:",
    "base-uri 'self'",
    "frame-ancestors 'none'",
].join('; ');

// Pure: inject the web-mode meta and CSP right after <head>. Idempotent — if the
// mode meta is already present the html is returned unchanged.
export function injectWebHead(html, { csp = WEB_CSP } = {}) {
    if (/name=["']proxion-mode["']/.test(html)) return html;
    const inject =
        `\n    <meta name="proxion-mode" content="web">` +
        `\n    <meta http-equiv="Content-Security-Policy" content="${csp}">`;
    // Insert after the opening <head> (tolerate attributes/whitespace).
    return html.replace(/<head(\s[^>]*)?>/i, (m) => m + inject);
}

// Files and dirs that must not ship in the static build.
const DENY = new Set([
    'node_modules', 'scripts', 'visual-baseline', 'vendor-src',
    'package.json', 'package-lock.json', 'eslint.config.mjs', 'vitest.config.js',
    'README.md',
]);
function _skip(src) {
    const base = src.replace(/\\/g, '/').split('/').pop();
    if (DENY.has(base)) return true;
    if (base.endsWith('.test.js')) return true;
    if (base.startsWith('smoke_')) return true;
    return false;
}

async function main() {
    const [srcDir, outDir] = process.argv.slice(2);
    if (!srcDir || !outDir) {
        console.error('usage: node scripts/build-web.mjs <srcDir> <outDir>');
        process.exit(2);
    }
    await mkdir(outDir, { recursive: true });
    await cp(srcDir, outDir, { recursive: true, filter: (s) => !_skip(s) });
    const idxPath = join(outDir, 'index.html');
    const html = await readFile(idxPath, 'utf8');
    await writeFile(idxPath, injectWebHead(html), 'utf8');
    console.log(`Built Proxion Web -> ${outDir} (web mode + CSP injected)`);
}

// Run only as a CLI, not when imported by the test.
if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith('build-web.mjs')) {
    main().catch((e) => { console.error(e); process.exit(1); });
}
