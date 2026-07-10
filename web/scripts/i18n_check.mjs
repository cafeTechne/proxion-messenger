// i18n completeness & safety check (PLAN_ROUND_56 F4) — a dependency-free gate
// that keeps translation keys honest. Runs in the web gate next to eslint.
//
//   node web/scripts/i18n_check.mjs
//
// HARD FAILS (exit 1):
//   - a referenced key (data-i18n / t() / tn() literal) missing from en.json
//   - any locale value containing markup ('<') — locale strings are plain text;
//     markup in a value would open an HTML-injection path (t() is un-escaped by
//     contract, escaped only at the sink)
//   - a non-en locale with a malformed JSON
//   - qps.json stale vs en.json
//
// WARNINGS (reported, non-fatal during the Phase G rollout):
//   - dead keys (in en.json, referenced nowhere)
//   - non-en locales missing keys present in en.json
//   - hardcoded string literals still sitting in known UI sinks (showToast, …)

import { readFileSync, existsSync, readdirSync } from 'fs';
import { resolve, dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { execFileSync } from 'child_process';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = resolve(HERE, '..');
const LOCALES = join(WEB, 'locales');

let fails = 0;
let warns = 0;
const fail = (m) => { console.error(`  ✗ ${m}`); fails++; };
const warn = (m) => { console.warn(`  ! ${m}`); warns++; };

// ── Load locales ────────────────────────────────────────────────────────────
function loadLocale(code) {
  const p = join(LOCALES, `${code}.json`);
  if (!existsSync(p)) return null;
  try { return JSON.parse(readFileSync(p, 'utf8')); }
  catch (e) { fail(`${code}.json is not valid JSON: ${e.message}`); return null; }
}

const en = loadLocale('en');
if (!en) { console.error('en.json missing or invalid — cannot check.'); process.exit(1); }
const enKeys = new Set(Object.keys(en).filter(k => k !== '_meta'));

// Keys referenced only by non-t() runtime code (so they aren't "dead" even
// though the extractor below won't see them). Keep this list tiny + justified.
const RUNTIME_KEYS = new Set([
  'push.newMessage',      // read by i18n.js _mirrorForServiceWorker → sw.js
  'push.newMessageFrom',
]);

// ── Extract referenced keys ─────────────────────────────────────────────────
const referenced = new Set();

// index.html: data-i18n="key" and data-i18n-attr="attr:key;attr2:key2"
const html = readFileSync(join(WEB, 'index.html'), 'utf8');
for (const m of html.matchAll(/data-i18n="([^"]+)"/g)) referenced.add(m[1].trim());
for (const m of html.matchAll(/data-i18n-attr="([^"]+)"/g)) {
  for (const pair of m[1].split(';')) {
    const idx = pair.indexOf(':');
    if (idx > 0) referenced.add(pair.slice(idx + 1).trim());
  }
}

// JS: t('key' / t("key" / tn('key' / tn("key" — literal first arg only
// (template literals with ${} are dynamic and intentionally skipped).
const jsFiles = readdirSync(WEB).filter(f => f.endsWith('.js') && !f.endsWith('.test.js'));
const T_RE = /\bt\(\s*['"]([^'"]+)['"]/g;
const TN_RE = /\btn\(\s*['"]([^'"]+)['"]/g;
const pluralBases = new Set();
const allSource = jsFiles.map(f => readFileSync(join(WEB, f), 'utf8')).join('\n');
for (const src of [allSource]) {
  for (const m of src.matchAll(T_RE)) referenced.add(m[1]);
  for (const m of src.matchAll(TN_RE)) {
    // tn keys live under key.one/.other; the base is referenced (for dead-key
    // detection) but completeness requires the plural VARIANTS, not the base.
    pluralBases.add(m[1]);
    referenced.add(`${m[1]}.other`); referenced.add(`${m[1]}.one`);
  }
}
// Indirect references: a key looked up through a code→key map (e.g. _errNice,
// _friendRequestErrors, _SIDEBAR_EMPTY) appears verbatim as a quoted string but
// not inside a t()/tn() call. Count any en.json key that shows up as a literal.
for (const key of enKeys) {
  if (allSource.includes(`"${key}"`) || allSource.includes(`'${key}'`)) referenced.add(key);
}

// ── (a) referenced-but-missing → FAIL ───────────────────────────────────────
for (const base of pluralBases) {
  if (!enKeys.has(`${base}.other`)) fail(`plural key "${base}" has no "${base}.other" in en.json`);
}
for (const key of referenced) {
  // plural variants are validated via their base above; only require .other.
  if (/\.(one|two|few|many|zero)$/.test(key)) continue;
  if (/\.other$/.test(key) && pluralBases.has(key.replace(/\.other$/, ''))) continue;
  if (!enKeys.has(key)) fail(`referenced key not in en.json: "${key}"`);
}

// ── (b) dead keys → WARN ────────────────────────────────────────────────────
for (const key of enKeys) {
  if (referenced.has(key)) continue;
  if (RUNTIME_KEYS.has(key)) continue;
  // A tn base (key.other present) counts as referenced if the base was used.
  const base = key.replace(/\.(one|two|few|many|zero|other)$/, '');
  if (base !== key && referenced.has(base)) continue;
  warn(`dead key (referenced nowhere): "${key}"`);
}

// ── markup safety (all locales) → FAIL ──────────────────────────────────────
const LOCALE_FILES = readdirSync(LOCALES).filter(f => f.endsWith('.json'));
for (const f of LOCALE_FILES) {
  const code = f.replace('.json', '');
  const data = loadLocale(code);
  if (!data) continue;
  for (const [k, v] of Object.entries(data)) {
    if (k === '_meta' || typeof v !== 'string') continue;
    if (v.includes('<')) fail(`${code}.json["${k}"] contains markup ('<') — locale values must be plain text`);
  }
}

// ── (c) non-en completeness → WARN ──────────────────────────────────────────
for (const f of LOCALE_FILES) {
  const code = f.replace('.json', '');
  if (code === 'en' || code === 'qps') continue;
  const data = loadLocale(code);
  if (!data) continue;
  const have = new Set(Object.keys(data).filter(k => k !== '_meta'));
  const missing = [...enKeys].filter(k => !have.has(k));
  if (missing.length) warn(`${code}.json missing ${missing.length} key(s), e.g. ${missing.slice(0, 3).join(', ')}`);
}

// ── qps freshness → FAIL ────────────────────────────────────────────────────
try {
  execFileSync('node', [join(HERE, 'gen_pseudo_locale.mjs'), '--check'], { stdio: 'pipe' });
} catch {
  fail('qps.json is stale — run `node web/scripts/gen_pseudo_locale.mjs`');
}

// ── (d) hardcoded-sink scan → WARN (rollout; flips to FAIL after Phase G) ────
const SINKS = [/showToast\(\s*["'][^"']/, /inlineNotice\(\s*["'][^"']/];
let hardcoded = 0;
for (const f of jsFiles) {
  const lines = readFileSync(join(WEB, f), 'utf8').split('\n');
  lines.forEach((ln) => { if (SINKS.some(re => re.test(ln))) hardcoded++; });
}
if (hardcoded) warn(`${hardcoded} hardcoded string(s) still in UI sinks (showToast/inlineNotice) — migrate in Phase G`);

// ── Result ──────────────────────────────────────────────────────────────────
console.log(`\ni18n_check: ${enKeys.size} canonical keys, ${referenced.size} referenced.`);
if (fails === 0) console.log(`✓ i18n_check passed${warns ? ` (${warns} warning(s))` : ''}.`);
else console.error(`✗ i18n_check: ${fails} failure(s), ${warns} warning(s).`);
process.exit(fails === 0 ? 0 : 1);
