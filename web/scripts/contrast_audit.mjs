// Contrast audit (PLAN_ROUND_56 D1): a dependency-free WCAG relative-luminance
// check over the palette-token pairs the UI actually paints, plus the generated
// per-user sender colors (webidColor). Fails (exit 1) on any pair below its
// threshold: 4.5:1 for normal text, 3:1 for large text / UI components
// (WCAG 1.4.3 + 1.4.11). Run in the web gate so palette edits can't regress.
//
// Token values are read from style.css itself, so there is no mirror to keep
// in sync. Every pair is checked in each theme variant: dark (the :root
// default), light (:root[data-theme="light"]), and both again under
// prefers-contrast: more. It also fails if the system-light block (the
// prefers-color-scheme media query) drifts from the explicit light block.
//
//   node web/scripts/contrast_audit.mjs

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const CSS_PATH = join(dirname(fileURLToPath(import.meta.url)), '..', 'style.css');

function srgbToLin(c) {
  c /= 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}
function lumHex(hex) {
  let h = hex.replace('#', '');
  if (h.length === 3) h = [...h].map((c) => c + c).join('');
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  return 0.2126 * srgbToLin(r) + 0.7152 * srgbToLin(g) + 0.0722 * srgbToLin(b);
}
function ratio(fg, bg) {
  const a = lumHex(fg), b = lumHex(bg);
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}
function hslToHex(h, s, l) {
  s /= 100; l /= 100;
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  const toHex = (x) => Math.round(255 * x).toString(16).padStart(2, '0');
  return `#${toHex(f(0))}${toHex(f(8))}${toHex(f(4))}`;
}

// ── Minimal CSS reader: every block with its chain of preludes and the
// custom-property declarations written directly inside it. ──
export function readBlocks(css) {
  css = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const blocks = [];
  const stack = [];          // { path, body }
  let buf = '';
  for (const ch of css) {
    if (ch === '{') {
      const prelude = buf.trim().replace(/\s+/g, ' ');
      buf = '';
      const parent = stack[stack.length - 1];
      if (parent) parent.body += ';';   // anything before a nested block is its own statement
      stack.push({ path: [...(parent ? parent.path : []), prelude], body: '' });
    } else if (ch === '}') {
      const top = stack.pop();
      if (!top) { buf = ''; continue; }
      top.body += buf; buf = '';
      const decls = {};
      for (const part of top.body.split(';')) {
        const m = part.match(/^\s*(--[\w-]+)\s*:\s*([\s\S]+?)\s*$/);
        if (m) decls[m[1]] = m[2];
      }
      blocks.push({ path: top.path, decls });
    } else {
      buf += ch;
      if (ch === ';') {
        if (stack.length) stack[stack.length - 1].body += buf;
        buf = '';
      }
    }
  }
  return blocks;
}

const pathIs = (b, ...p) => b.path.length === p.length && b.path.every((x, i) => x === p[i]);
const merge = (blocks, ...p) => Object.assign({}, ...blocks.filter((b) => pathIs(b, ...p)).map((b) => b.decls));

export function themeTokens(css) {
  const blocks = readBlocks(css);
  const dark = merge(blocks, ':root');
  const lightOnly = merge(blocks, ':root[data-theme="light"]');
  const systemLight = merge(blocks, '@media (prefers-color-scheme: light)', ':root:not([data-theme="dark"])');
  const more = merge(blocks, '@media (prefers-contrast: more)', ':root[data-theme], :root:not([data-theme])');
  const light = { ...dark, ...lightOnly };
  return {
    lightOnly, systemLight,
    variants: {
      dark,
      light,
      'dark + more contrast': { ...dark, ...more },
      'light + more contrast': { ...light, ...more },
    },
  };
}

export function resolve(tokens, name, seen = new Set()) {
  if (seen.has(name)) throw new Error(`cycle at ${name}`);
  seen.add(name);
  const v = tokens[name];
  if (v === undefined) throw new Error(`${name} is not defined`);
  const m = v.match(/^var\(\s*(--[\w-]+)\s*(?:,[^)]*)?\)$/);
  if (m) return resolve(tokens, m[1], seen);
  // color-mix(in srgb, var(--a) P%, var(--b)) between two opaque hex tokens.
  const mix = v.match(/^color-mix\(in srgb,\s*var\((--[\w-]+)\)\s+([\d.]+)%,\s*var\((--[\w-]+)\)\)$/);
  if (mix) {
    const p = parseFloat(mix[2]) / 100;
    const a = resolve(tokens, mix[1], new Set(seen)), b = resolve(tokens, mix[3], new Set(seen));
    const ch = (h, i) => parseInt(h.slice(1 + 2 * i, 3 + 2 * i), 16);
    return '#' + [0, 1, 2].map((i) => Math.round(ch(a, i) * p + ch(b, i) * (1 - p)).toString(16).padStart(2, '0')).join('');
  }
  return v;
}

// [label, fg token, bg token, minRatio]. minRatio 3 for large text / non-text UI.
const PAIRS = [
  ['primary text on bg', '--text-primary', '--bg-primary', 4.5],
  ['secondary text on bg', '--text-secondary', '--bg-primary', 4.5],
  ['secondary text on secondary bg', '--text-secondary', '--bg-secondary', 4.5],
  ['secondary text on accent bg', '--text-secondary', '--bg-accent', 4.5],
  ['primary text on active row (surface-3)', '--text-primary', '--surface-3', 4.5],
  ['on-accent text on accent button', '--color-on-accent', '--accent', 4.5],
  ['on-status text on solid danger button', '--color-on-status', '--color-danger-strong', 4.5],
  ['accent-text link on secondary bg', '--accent-text', '--bg-secondary', 4.5],
  ['accent-text link on bg', '--accent-text', '--bg-primary', 4.5],
  ['accent-text link on surface-2', '--accent-text', '--surface-2', 4.5],
  ['slate-500 status text on secondary bg', '--slate-500', '--bg-secondary', 4.5],
  ['slate-600 small text on bg', '--slate-600', '--bg-primary', 4.5],
  ['slate-600 small text on secondary bg', '--slate-600', '--bg-secondary', 4.5],
  ['slate-300 settings text on surface-1', '--slate-300', '--surface-1', 4.5],
  ['danger-soft error text on bg', '--color-danger-soft', '--bg-primary', 4.5],
  ['success-soft text on bg', '--color-success-soft', '--bg-primary', 4.5],
  ['success-soft text on secondary bg', '--color-success-soft', '--bg-secondary', 4.5],
  ['danger icon on surface-2: UI component', '--color-danger', '--surface-2', 3],
  ['danger-soft menu text on hovered danger row', '--color-danger-soft', '--danger-row-hover', 4.5],
  ['accent (link/focus ring) on bg: UI component', '--accent', '--bg-primary', 3],
  ['accent focus ring on surface-1: UI component', '--accent', '--surface-1', 3],
  ['accent-text caret on input (slate-900): UI component', '--accent-text', '--slate-900', 3],
  ['on-status text on success toast', '--color-on-status', '--color-success-bg', 4.5],
  ['on-status text on error toast / danger banner', '--color-on-status', '--color-danger-bg', 4.5],
  ['warning text on warning toast / banner', '--color-warning-text', '--color-warning-bg', 4.5],
  ['info text on info banner', '--color-info-text', '--color-info-bg', 4.5],
  ['slate-50 on info toast', '--slate-50', '--surface-2', 4.5],
  ['warning-soft text on bg', '--color-warning-soft', '--bg-primary', 4.5],
  ['warning-soft text on secondary bg', '--color-warning-soft', '--bg-secondary', 4.5],
  ['warning-soft claim on friend-request card', '--color-warning-soft', '--surface-2', 4.5],
  ['primary text on friend-request card', '--text-primary', '--surface-2', 4.5],
  ['secondary text on surface-2 (badges, chips)', '--text-secondary', '--surface-2', 4.5],
  ['slate-600 meta text on surface-2', '--slate-600', '--surface-2', 4.5],
  ['success-soft on verified tint', '--color-success-soft', '--color-success-tint', 4.5],
  ['slate-50 on slate button / unread badge', '--slate-50', '--slate-700', 4.5],
  ['slate-50 on input (slate-900)', '--slate-50', '--slate-900', 4.5],
  ['slate-900 on update install button', '--slate-900', '--color-success-soft', 4.5],
  ['danger-soft text on surface-2', '--color-danger-soft', '--surface-2', 4.5],
  ['warning (pod note) text on bg', '--color-warning', '--bg-primary', 4.5],
  ['on-accent text on admin role badge', '--color-on-accent', '--role-admin', 4.5],
  ['on-accent text on mod role badge', '--color-on-accent', '--role-mod', 4.5],
  ['online dot on sidebar: UI component', '--color-success', '--surface-1', 3],
  ['away dot on sidebar: UI component', '--color-presence-away', '--surface-1', 3],
  ['busy dot on sidebar: UI component', '--color-danger', '--surface-1', 3],
  ['selected text on selection fill', '--selection-text', '--selection-bg', 4.5],
  ['selection fill stands out from bg', '--selection-bg', '--bg-primary', 1.5],
  ['placeholder (slate-500) on slate-900 input', '--slate-500', '--slate-900', 4.5],
  ['placeholder (slate-500) on surface-2 input', '--slate-500', '--surface-2', 4.5],
];

const WEBID_SAT = 55;   // must match util.js webidColor

function main() {
  const css = readFileSync(CSS_PATH, 'utf8');
  const { lightOnly, systemLight, variants } = themeTokens(css);
  let fails = 0;

  // The system-light block must be a copy of the explicit light block.
  const a = JSON.stringify(Object.entries(lightOnly).sort());
  const b = JSON.stringify(Object.entries(systemLight).sort());
  if (!Object.keys(lightOnly).length) {
    fails++; console.error('✗ no :root[data-theme="light"] block found in style.css');
  } else if (a !== b) {
    fails++;
    const keys = new Set([...Object.keys(lightOnly), ...Object.keys(systemLight)]);
    const diff = [...keys].filter((k) => lightOnly[k] !== systemLight[k]);
    console.error(`✗ light theme blocks differ (data-theme vs prefers-color-scheme): ${diff.join(', ')}`);
  } else {
    console.log(`✓ light theme blocks match (${Object.keys(lightOnly).length} tokens)`);
  }

  for (const [name, tokens] of Object.entries(variants)) {
    console.log(`\nPalette-token contrast, ${name}:`);
    for (const [label, fgName, bgName, min] of PAIRS) {
      let fg, bg;
      try { fg = resolve(tokens, fgName); bg = resolve(tokens, bgName); } catch (e) {
        fails++; console.log(`  ✗ ${label}: ${e.message}`); continue;
      }
      if (!/^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$/.test(fg) || !/^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$/.test(bg)) {
        fails++; console.log(`  ✗ ${label}: not a plain hex color [${fg} on ${bg}]`); continue;
      }
      const r = ratio(fg, bg);
      const ok = r >= min;
      if (!ok) fails++;
      console.log(`  ${ok ? '✓' : '✗'} ${r.toFixed(2)} (need ${min})  ${label}  [${fg} on ${bg}]`);
    }

    // .mention chip inside a .mention-highlight message: the accent laid over
    // the feed at 10% (row wash), then 22% (chip). Keep in step with style.css.
    {
      const over = (fgHex, bgHex, p) => '#' + [0, 1, 2].map((i) => {
        const c = (h) => parseInt(h.slice(1 + 2 * i, 3 + 2 * i), 16);
        return Math.round(c(fgHex) * p + c(bgHex) * (1 - p)).toString(16).padStart(2, '0');
      }).join('');
      const accent = resolve(tokens, '--accent');
      const chip = over(accent, over(accent, resolve(tokens, '--bg-primary'), 0.10), 0.22);
      const fg = resolve(tokens, '--accent-text');
      const r = ratio(fg, chip);
      if (r < 4.5) fails++;
      console.log(`  ${r >= 4.5 ? '✓' : '✗'} ${r.toFixed(2)} (need 4.5)  accent-text @mention chip on a highlighted message  [${fg} on ${chip}]`);
    }

    // webidColor(): hsl(hue, 55%, var(--webid-l)). Every generated sender color
    // must pass on the feed background. Find the worst hue.
    const light = parseFloat(resolve(tokens, '--webid-l'));
    const feed = resolve(tokens, '--bg-primary');
    let worst = { r: Infinity, hue: 0 };
    for (let hue = 0; hue < 360; hue++) {
      const r = ratio(hslToHex(hue, WEBID_SAT, light), feed);
      if (r < worst.r) worst = { r, hue };
    }
    const webidOk = worst.r >= 4.5;
    if (!webidOk) fails++;
    console.log(`  ${webidOk ? '✓' : '✗'} ${worst.r.toFixed(2)} (need 4.5)  webidColor hsl(*, ${WEBID_SAT}%, ${light}%) worst hue ${worst.hue} on ${feed}`);
  }

  if (fails === 0) console.log('\n✓ contrast: all pairs pass in every theme.');
  else console.error(`\n✗ contrast: ${fails} check(s) failed.`);
  process.exit(fails === 0 ? 0 : 1);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) main();
