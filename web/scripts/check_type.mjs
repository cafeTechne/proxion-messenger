// Type scale check: font-size, line-height, font-weight and letter-spacing in
// style.css use the tokens defined in its :root block (--text-*, --leading-*,
// --weight-*, --tracking-*). Fails (exit 1) on a literal, unless the rule is in
// ALLOW below.
//
//   node web/scripts/check_type.mjs
//
// Allowed without a token: em sizes (relative to the surrounding text, e.g.
// code and emoji), inherit, and line-height 0 (strips the inline gap under an
// icon-only button). Allowlist entries are [selector, property, reason].

import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';

const ALLOW = [
  ['#chat-header-name', 'font-size', 'print: points are the unit on paper'],
];

const css = readFileSync(fileURLToPath(new URL('../style.css', import.meta.url)), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' '));   // keep line numbers

const RULES = {
  'font-size': { token: /^var\(--text-[a-z0-9]+\)$/, ok: /^(inherit|\d*\.?\d+em|clamp\(var\(--text-[a-z0-9]+\),[^,]+,\s*var\(--text-[a-z0-9]+\)\))$/ },
  'line-height': { token: /^var\(--leading-[a-z]+\)$/, ok: /^(0|inherit|var\(--avatar-[a-z]+\)|calc\(.*var\(--leading-[a-z]+\).*\))$/ },
  'font-weight': { token: /^var\(--weight-[a-z]+\)$/, ok: /^inherit$/ },
  'letter-spacing': { token: /^var\(--tracking-[a-z]+\)$/, ok: /^(0|normal)$/ },
};

const allowed = (sel, prop) => ALLOW.some(([s, p]) => s === sel && p === prop);
const bad = [];
const ruleRe = /([^{}]*)\{([^{}]*)\}/g;
let m;
while ((m = ruleRe.exec(css))) {
  const sel = m[1].trim().replace(/\s+/g, ' ');
  if (sel.startsWith(':root') || sel.startsWith('@') || /^(from|to|\d+%)/.test(sel)) continue;
  const bodyStart = m.index + m[1].length + 1;
  const declRe = /(?:^|[;\s])(font-size|line-height|font-weight|letter-spacing)\s*:\s*([^;}]+)/g;
  let d;
  while ((d = declRe.exec(m[2]))) {
    const prop = d[1];
    const val = d[2].trim().replace(/\s*!important$/, '');
    const rule = RULES[prop];
    if (rule.token.test(val) || rule.ok.test(val) || allowed(sel, prop)) continue;
    const line = css.slice(0, bodyStart + d.index).split('\n').length;
    bad.push(`  style.css:${line}  ${sel} { ${prop}: ${val} }`);
  }
}

if (bad.length) {
  console.error(`✗ type: ${bad.length} declaration(s) off the type scale:`);
  console.error(bad.join('\n'));
  console.error('Use var(--text-*), var(--leading-*), var(--weight-*) or var(--tracking-*), or add an entry to ALLOW in scripts/check_type.mjs with a reason.');
  process.exit(1);
}
console.log('✓ type: font-size, line-height, font-weight and letter-spacing in style.css are on the type scale.');
