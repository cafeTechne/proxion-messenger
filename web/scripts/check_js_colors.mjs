// Fails when a web/*.js module paints with a hex or rgb()/rgba() color literal.
// Colors belong in style.css as tokens (--slate-*, --color-*, --surface-*) or
// classes; JS should set a class or a var(--token) string. Runtime-computed
// colors (hsl avatars) are not literals and pass. The allowlist below covers
// the few places where a literal is the point (pixels baked into an image).
//
//   node web/scripts/check_js_colors.mjs

import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = join(dirname(fileURLToPath(import.meta.url)), '..');

// file -> reason. Every literal in these files is allowed.
const ALLOW = {
  'address.js': 'QR code module colors: fixed dark-on-white for scanner contrast',
  'pairing.js': 'QR code module colors: fixed dark-on-white for scanner contrast',
  'meme.js': 'canvas drawing: white text with black outline baked into the image',
};
const SKIP = new Set(['solid-authn.bundle.js']);

// Hex not followed by a word char or "-" (so "#feed-notice" selectors pass).
const HEX = /#[0-9a-fA-F]{3,8}(?![\w-])/g;
const RGB = /\brgba?\s*\(/g;

let fails = 0;
const files = readdirSync(WEB).filter((f) => f.endsWith('.js') && !f.endsWith('.test.js') && !SKIP.has(f));
for (const f of files.sort()) {
  if (ALLOW[f]) continue;
  const lines = readFileSync(join(WEB, f), 'utf8').split(/\r?\n/);
  lines.forEach((line, i) => {
    const s = line.trim();
    if (s.startsWith('//') || s.startsWith('*') || s.startsWith('/*')) return;
    const hits = [...(line.match(HEX) || []), ...(line.match(RGB) || [])];
    // "&#x2713;" style HTML entities are not colors.
    const real = hits.filter((h) => !line.includes('&' + h));
    if (real.length) {
      fails++;
      console.log(`  ${f}:${i + 1}  ${real.join(', ')}`);
    }
  });
}

if (fails) {
  console.log(`\n${fails} line(s) with color literals in web/*.js. Use a token from style.css (var(--...)) or a class.`);
  process.exit(1);
}
console.log(`check:js-colors: ${files.length} files clean (${Object.keys(ALLOW).length} allowlisted).`);
