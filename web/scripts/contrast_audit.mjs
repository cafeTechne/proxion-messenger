// Contrast audit (PLAN_ROUND_56 D1) — a dependency-free WCAG relative-luminance
// check over the palette-token pairs the UI actually paints, plus the generated
// per-user sender colors (webidColor). Fails (exit 1) on any pair below its
// threshold: 4.5:1 for normal text, 3:1 for large text / UI components
// (WCAG 1.4.3 + 1.4.11). Run in the web gate so palette edits can't regress.
//
//   node web/scripts/contrast_audit.mjs

function srgbToLin(c) {
  c /= 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}
function lumHex(hex) {
  const h = hex.replace('#', '');
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

// Resolved token values (mirror of style.css :root). Keep in sync when tokens change.
const T = {
  bgPrimary: '#1a1a2e', bgSecondary: '#16213e', bgAccent: '#0f3460',
  textPrimary: '#e1e1e1', textSecondary: '#94a3b8',
  accent: '#d61f52',                 // brand accent (darkened in D1 for AA on white text)
  accentText: '#f06a8a',             // accent used AS link text on dark surfaces
  colorSuccess: '#22c55e', colorSuccessSoft: '#4ade80',
  colorDanger: '#ef4444', colorDangerSoft: '#f87171',
  slate300: '#cbd5e1', slate400: '#94a3b8',
  slate500: '#8598ae',               // bumped from #64748b (D1) — used as body text
  slate600: '#8091a7',               // bumped from #475569 (D1) so small secondary text passes
  white: '#ffffff',
};

// [label, fg, bg, minRatio]. minRatio 3 for large text / non-text UI.
const PAIRS = [
  ['primary text on bg', T.textPrimary, T.bgPrimary, 4.5],
  ['secondary text on bg', T.textSecondary, T.bgPrimary, 4.5],
  ['secondary text on secondary bg', T.textSecondary, T.bgSecondary, 4.5],
  ['secondary text on accent bg', T.textSecondary, T.bgAccent, 4.5],
  ['white on accent button', T.white, T.accent, 4.5],
  ['accent-text link on secondary bg', T.accentText, T.bgSecondary, 4.5],
  ['accent-text link on bg', T.accentText, T.bgPrimary, 4.5],
  ['slate-500 status text on secondary bg', T.slate500, T.bgSecondary, 4.5],
  ['slate-600 small text on bg', T.slate600, T.bgPrimary, 4.5],
  ['slate-600 small text on secondary bg', T.slate600, T.bgSecondary, 4.5],
  ['danger-soft error text on bg', T.colorDangerSoft, T.bgPrimary, 4.5],
  ['success-soft text on bg', T.colorSuccessSoft, T.bgPrimary, 4.5],
  ['accent (link/focus ring) on bg — UI component', T.accent, T.bgPrimary, 3],
];

let fails = 0;
console.log('Palette-token contrast:');
for (const [label, fg, bg, min] of PAIRS) {
  const r = ratio(fg, bg);
  const ok = r >= min;
  if (!ok) fails++;
  console.log(`  ${ok ? '✓' : '✗'} ${r.toFixed(2)} (need ${min})  ${label}  [${fg} on ${bg}]`);
}

// webidColor(): hsl(hue, 55%, L). Every generated sender color must pass on the
// darkest feed background. Find the worst hue.
const WEBID_SAT = 55, WEBID_LIGHT = 68;   // must match util.js webidColor
let worst = { r: Infinity, hue: 0 };
for (let hue = 0; hue < 360; hue++) {
  const r = ratio(hslToHex(hue, WEBID_SAT, WEBID_LIGHT), T.bgPrimary);
  if (r < worst.r) worst = { r, hue };
}
const webidOk = worst.r >= 4.5;
if (!webidOk) fails++;
console.log(`\nwebidColor hsl(*, ${WEBID_SAT}%, ${WEBID_LIGHT}%) worst hue ${worst.hue}: ${worst.r.toFixed(2)} (need 4.5) ${webidOk ? '✓' : '✗'}`);

if (fails === 0) console.log('\n✓ contrast: all pairs pass.');
else console.error(`\n✗ contrast: ${fails} pair(s) below threshold.`);
process.exit(fails === 0 ? 0 : 1);
