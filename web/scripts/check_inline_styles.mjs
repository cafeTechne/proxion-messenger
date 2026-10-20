// Inline style check. Looks belong in style.css as component classes; the
// markup and scripts only switch them.
//
//   node web/scripts/check_inline_styles.mjs
//
// Fails (exit 1) on:
//   - a style="" in index.html other than the display:none the scripts toggle
//   - a style="" in a web/*.js template that sets no runtime value (${...})
//   - an element.style.cssText assignment in web/*.js
// Per-property element.style.x = ... stays allowed: show/hide, positions and
// sizes computed at runtime.

import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = join(dirname(fileURLToPath(import.meta.url)), '..');
const SKIP = new Set(['solid-authn.bundle.js']);
const bad = [];

const html = readFileSync(join(WEB, 'index.html'), 'utf8');
for (const m of html.matchAll(/\sstyle="([^"]*)"/g)) {
  if (/^\s*display:\s*none;?\s*$/.test(m[1])) continue;
  const line = html.slice(0, m.index).split('\n').length;
  bad.push(`  index.html:${line}  style="${m[1].replace(/\s+/g, ' ').slice(0, 90)}"`);
}

const files = readdirSync(WEB).filter((f) => f.endsWith('.js') && !f.endsWith('.test.js') && !SKIP.has(f));
for (const f of files.sort()) {
  const lines = readFileSync(join(WEB, f), 'utf8').split(/\r?\n/);
  lines.forEach((line, i) => {
    const s = line.trim();
    if (s.startsWith('//') || s.startsWith('*')) return;
    if (/\.style\.cssText\s*=/.test(line)) bad.push(`  ${f}:${i + 1}  style.cssText assignment`);
    for (const m of line.matchAll(/style=\\?"([^"\\]*)\\?"/g)) {
      if (!m[1].includes('${')) bad.push(`  ${f}:${i + 1}  static style="${m[1].slice(0, 80)}"`);
    }
  });
}

if (bad.length) {
  console.error(`✗ inline styles: ${bad.length} static style(s) outside style.css:`);
  console.error(bad.join('\n'));
  console.error('Move the look into a class in style.css (see the shared components block) and set the class instead.');
  process.exit(1);
}
console.log(`✓ inline styles: index.html and ${files.length} scripts only toggle display or set runtime values.`);
