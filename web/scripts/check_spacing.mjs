// Spacing check: padding, margin and gap in style.css use the --space-* scale.
// Fails (exit 1) on any px literal in those properties other than 0, 1 or 2
// (hairlines and optical nudges), unless the rule is in ALLOW below.
//
//   node web/scripts/check_spacing.mjs
//
// Allowlist entries are [selector, property, reason]. Keep it short: prefer a
// token, and add an entry only when the exact px value is the point.

import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';

const ALLOW = [
  ['.feed-welcome', 'padding', 'roomy first-run welcome, larger than the top of the scale'],
  ['.empty-state', 'padding', 'roomy empty state, larger than the top of the scale'],
  ['main > #message-feed', 'padding-inline', '960px is the reading column width, not a spacing step'],
  ['main > #message-form, main > #reply-bar, main > #typing-indicator', 'padding-inline', 'same 960px column'],
];

const css = readFileSync(fileURLToPath(new URL('../style.css', import.meta.url)), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' '));   // keep line numbers

const allowed = (sel, prop) => ALLOW.some(([s, p]) => s === sel && p === prop);
const bad = [];
const ruleRe = /([^{}]*)\{([^{}]*)\}/g;
let m;
while ((m = ruleRe.exec(css))) {
  const sel = m[1].trim().replace(/\s+/g, ' ');
  if (sel.startsWith(':root') || sel.startsWith('@')) continue;
  const bodyStart = m.index + m[1].length + 1;
  const declRe = /(?:^|[;\s])((?:padding|margin|gap|row-gap|column-gap)(?:-[a-z-]+)?)\s*:\s*([^;}]+)/g;
  let d;
  while ((d = declRe.exec(m[2]))) {
    const [, prop, val] = d;
    const pxs = [...val.matchAll(/(-?\d+(?:\.\d+)?)px\b/g)].map(x => Math.abs(parseFloat(x[1])));
    if (pxs.every(n => n <= 2) || allowed(sel, prop)) continue;
    const line = css.slice(0, bodyStart + d.index).split('\n').length;
    bad.push(`  style.css:${line}  ${sel} { ${prop}: ${val.trim()} }`);
  }
}

if (bad.length) {
  console.error(`✗ spacing: ${bad.length} padding/margin/gap px value(s) off the --space-* scale:`);
  console.error(bad.join('\n'));
  console.error('Use var(--space-1..6), or add an entry to ALLOW in scripts/check_spacing.mjs with a reason.');
  process.exit(1);
}
console.log('✓ spacing: padding, margin and gap in style.css are on the --space-* scale.');
