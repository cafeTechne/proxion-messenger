// Visual-regression smoke test (ROADMAP_2 / H3) — guards the Phase F design-system
// work (and any future UI change) against silent layout/color breakage that the
// eval-error smoke (smoke_browser) can't see.
//
// Screenshots key screens in the running app and pixel-diffs them against committed
// baselines (web/visual-baseline/*.png) with a small tolerance. The settled UI is
// deterministic on a given machine, so a near-exact match is a strong guard.
//
//   node web/smoke_visual.mjs            # compare against baselines (CI/verify)
//   node web/smoke_visual.mjs --update   # (re)write baselines after an intended change
//
// Prereq: gateway running. Chrome/Edge installed. On failure, the actual + a diff
// image are written to artifacts/ for inspection. Exit 0 = match, 1 = drift.

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs';
import { dirname } from 'path';
import puppeteer from 'puppeteer-core';
import { PNG } from 'pngjs';
import pixelmatch from 'pixelmatch';

const URL = process.env.PROXION_SMOKE_URL || 'https://localhost:8080/';
const UPDATE = process.argv.includes('--update');
const BASELINE_DIR = 'visual-baseline';
const ARTIFACT_DIR = '../artifacts';
const TOLERANCE = 0.001;            // max fraction of pixels allowed to differ (0.1%)
const CHROME = [
  process.env.PROXION_CHROME,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
].filter(Boolean).find(p => p && existsSync(p));
if (!CHROME) { console.error('No Chrome/Edge found; set PROXION_CHROME.'); process.exit(2); }

// Screens to guard. `prepare` runs in-page to reach a deterministic, settled view.
const SCREENS = [
  { name: 'main', prepare: null },
  { name: 'settings', prepare: () => document.getElementById('settings-btn')?.click() },
];

function ensureDir(p) { mkdirSync(dirname(p), { recursive: true }); }

const browser = await puppeteer.launch({
  executablePath: CHROME, headless: 'new',
  args: ['--ignore-certificate-errors', '--no-sandbox', '--disable-gpu', '--force-color-profile=srgb'],
});
let failures = 0, updated = 0;
try {
  for (const screen of SCREENS) {
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 800, deviceScaleFactor: 1 });
    await page.goto(URL, { waitUntil: 'load', timeout: 20000 });
    await new Promise(r => setTimeout(r, 3000));   // let the bootstrap settle
    if (screen.prepare) { await page.evaluate(screen.prepare); await new Promise(r => setTimeout(r, 600)); }
    // Freeze animations/transitions + hide the text caret so the screenshot is
    // deterministic regardless of animation phase. Without this, continuously
    // animated elements (e.g. the skeleton-loader shimmer gradient) land at a
    // different phase each run and produce flaky pixel diffs unrelated to any
    // actual change. The history skeleton is additionally hidden outright: it's
    // transient loading chrome added/removed around the connect race, so its
    // mere presence (and shimmer phase) at the 3 s capture mark is nondeterministic.
    await page.addStyleTag({ content:
      '*, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; }' +
      '#history-skeleton { display: none !important; }' +
      // The NAT banner depends on an async /connectivity fetch that races the
      // capture, and it now shifts layout (pushes the app down) — so a CSS kill
      // is the only ordering-proof suppression (a DOM remove loses the race).
      '#nat-warning-banner { display: none !important; }' });
    // Normalize per-session-volatile content (a fresh headless launch mints a new
    // did:key identity and the pod/address status resolves live) so the diff
    // reflects layout/colour, not values that legitimately change every run.
    await page.evaluate(() => {
      for (const id of ['settings-did', 'settings-proxion-address']) {
        const e = document.getElementById(id); if (e) e.textContent = '—';
      }
      const dot = document.getElementById('settings-pod-status-dot');
      if (dot) { dot.textContent = '● status'; dot.style.color = '#64748b'; }
    });
    await new Promise(r => setTimeout(r, 120));    // let the freeze take effect
    const shot = await page.screenshot();          // Buffer (PNG)
    await page.close();

    const baselinePath = `${BASELINE_DIR}/${screen.name}.png`;
    if (UPDATE || !existsSync(baselinePath)) {
      ensureDir(baselinePath);
      writeFileSync(baselinePath, shot);
      console.log(`  [baseline] wrote ${baselinePath}`);
      updated++;
      continue;
    }

    const actual = PNG.sync.read(shot);
    const baseline = PNG.sync.read(readFileSync(baselinePath));
    if (actual.width !== baseline.width || actual.height !== baseline.height) {
      console.error(`  ✗ ${screen.name}: size changed ${baseline.width}x${baseline.height} -> ${actual.width}x${actual.height}`);
      failures++;
      continue;
    }
    const diff = new PNG({ width: actual.width, height: actual.height });
    const mismatched = pixelmatch(actual.data, baseline.data, diff.data, actual.width, actual.height, { threshold: 0.1 });
    const frac = mismatched / (actual.width * actual.height);
    if (frac > TOLERANCE) {
      ensureDir(`${ARTIFACT_DIR}/x`);
      writeFileSync(`${ARTIFACT_DIR}/visual-${screen.name}-actual.png`, shot);
      writeFileSync(`${ARTIFACT_DIR}/visual-${screen.name}-diff.png`, PNG.sync.write(diff));
      console.error(`  ✗ ${screen.name}: ${mismatched} px differ (${(frac * 100).toFixed(3)}%) — see artifacts/visual-${screen.name}-{actual,diff}.png`);
      failures++;
    } else {
      console.log(`  ✓ ${screen.name}: ${mismatched} px differ (${(frac * 100).toFixed(3)}%) — within tolerance`);
    }
  }
} finally {
  await browser.close();
}

if (updated) console.log(`\nBaselines written: ${updated}. Re-run without --update to verify.`);
if (failures) { console.error(`\n✗ Visual regression: ${failures} screen(s) drifted. If intended, re-run with --update.`); process.exitCode = 1; }
else if (!updated) console.log('\n✓ No visual regressions.');
