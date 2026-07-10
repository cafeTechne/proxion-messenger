// Pseudo-locale generator (PLAN_ROUND_56 F2/PL1) — builds locales/qps.json from
// the canonical en.json. The pseudo-locale accents every letter, pads length by
// ~40% (catches layouts that assume English-width text), and brackets each
// string with ⟦ ⟧ (catches truncation and un-externalized concatenation). It is
// TEST-ONLY: never listed in SUPPORTED_LOCALES, only reachable via the explicit
// localStorage override the pseudo-locale smoke sets.
//
//   node web/scripts/gen_pseudo_locale.mjs        # writes locales/qps.json
//   node web/scripts/gen_pseudo_locale.mjs --check # verify it's up to date (CI)

import { readFileSync, writeFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const HERE = dirname(fileURLToPath(import.meta.url));
const LOCALES = resolve(HERE, '..', 'locales');

const MAP = {
  a: 'á', b: 'ḅ', c: 'ç', d: 'ð', e: 'é', f: 'ƒ', g: 'ǧ', h: 'ĥ', i: 'í', j: 'ĵ',
  k: 'ķ', l: 'ĺ', m: 'ɱ', n: 'ñ', o: 'ó', p: 'ρ', q: 'ǫ', r: 'ŕ', s: 'š', t: 'ţ',
  u: 'ú', v: 'ṽ', w: 'ŵ', x: 'ẋ', y: 'ý', z: 'ž',
  A: 'Á', B: 'Ḅ', C: 'Ç', D: 'Ð', E: 'É', F: 'Ƒ', G: 'Ǧ', H: 'Ĥ', I: 'Í', J: 'Ĵ',
  K: 'Ķ', L: 'Ĺ', M: 'Ṁ', N: 'Ñ', O: 'Ó', P: 'Ρ', Q: 'Ǫ', R: 'Ŕ', S: 'Š', T: 'Ţ',
  U: 'Ú', V: 'Ṽ', W: 'Ŵ', X: 'Ẋ', Y: 'Ý', Z: 'Ž',
};

// Accent a string but leave {placeholders} and leading/trailing markup-ish runs
// untouched so interpolation keys keep working.
function accent(str) {
  let out = '';
  let inBrace = false;
  for (const ch of str) {
    if (ch === '{') inBrace = true;
    else if (ch === '}') inBrace = false;
    out += inBrace ? ch : (MAP[ch] || ch);
  }
  return out;
}

// Pad by ~40% using a repeated vowel run appended before the closing bracket.
function pad(str) {
  const extra = Math.max(1, Math.round(str.replace(/\{[^}]*\}/g, '').length * 0.4));
  return 'x́'.repeat(0) + 'ᴇ'.repeat(0) + str + ' ' + 'áéíóú'.repeat(Math.ceil(extra / 5)).slice(0, extra);
}

function pseudo(value) {
  return `⟦${pad(accent(value))}⟧`;
}

function build() {
  const en = JSON.parse(readFileSync(resolve(LOCALES, 'en.json'), 'utf8'));
  const out = { _meta: { locale: 'qps', name: 'Pseudo (test)', generated: true, source: 'en.json' } };
  for (const [k, v] of Object.entries(en)) {
    if (k === '_meta') continue;
    out[k] = typeof v === 'string' ? pseudo(v) : v;
  }
  return JSON.stringify(out, null, 2) + '\n';
}

const target = resolve(LOCALES, 'qps.json');
const generated = build();

if (process.argv.includes('--check')) {
  let current = '';
  try { current = readFileSync(target, 'utf8'); } catch { /* missing */ }
  // Compare line-ending-insensitively so a CRLF checkout (Windows / git
  // autocrlf) doesn't spuriously report the generated file as stale.
  const norm = (s) => s.replace(/\r\n/g, '\n');
  if (norm(current) !== norm(generated)) {
    console.error('✗ qps.json is stale — run `node web/scripts/gen_pseudo_locale.mjs`.');
    process.exit(1);
  }
  console.log('✓ qps.json is up to date.');
} else {
  writeFileSync(target, generated);
  console.log(`✓ wrote ${target}`);
}
