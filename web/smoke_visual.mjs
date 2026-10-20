// Visual-regression smoke: screenshots the main desktop views in both themes
// and pixel-diffs them against committed baselines (web/visual-baseline/*.png).
//
// Views: empty state, settings, a dialog, a room with messages, the room with
// the members panel, and an E2E DM. Each is captured dark and light at
// 1280x800, so a change to tokens, components or layout shows up here.
//
//   node web/smoke_visual.mjs                 # compare against baselines
//   node web/smoke_visual.mjs --update        # (re)write baselines after an intended change
//   node web/smoke_visual.mjs --out <dir>     # just write the screenshots to <dir>
//
// Starts two throwaway gateways (plain http on 127.0.0.1, pod off) so the DM
// view can be reached through a real friend request. Values that change every
// run (times, DIDs, safety numbers, per-identity colours) are normalized before
// capture. Baselines depend on the machine's font rendering, so this runs
// locally (npm run smoke:all), not in CI. On failure the actual and a diff image
// are written to artifacts/.

import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs';
import { spawn } from 'child_process';
import { createServer, connect } from 'net';
import { tmpdir } from 'os';
import { join, resolve } from 'path';
import { fileURLToPath } from 'url';
import puppeteer from 'puppeteer-core';
import { PNG } from 'pngjs';
import pixelmatch from 'pixelmatch';

const UPDATE = process.argv.includes('--update');
const OUT_IDX = process.argv.indexOf('--out');
const OUT_DIR = OUT_IDX > 0 ? process.argv[OUT_IDX + 1] : null;
const WEB = resolve(fileURLToPath(new URL('.', import.meta.url)));
const REPO = resolve(WEB, '..');
const BASELINE_DIR = join(WEB, 'visual-baseline');
const ARTIFACT_DIR = join(REPO, 'artifacts');
const TOLERANCE = 0.002;            // max fraction of pixels allowed to differ (0.2%)
const THEMES = ['dark', 'light'];
const CHROME = [
  process.env.PROXION_CHROME,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
].filter(Boolean).find(p => p && existsSync(p));
if (!CHROME) { console.error('No Chrome/Edge found; set PROXION_CHROME.'); process.exit(2); }

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const freePort = () => new Promise((res, rej) => {
  const s = createServer();
  s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
  s.on('error', rej);
});
const portUp = (port) => new Promise((res) => {
  const c = connect(port, '127.0.0.1');
  c.once('connect', () => { c.destroy(); res(true); });
  c.once('error', () => { c.destroy(); res(false); });
});

const procs = [], dirs = [];
async function startGateway(name) {
  const httpPort = await freePort(), wsPort = await freePort();
  const dataDir = mkdtempSync(join(tmpdir(), `proxion-visual-${name}-`));
  dirs.push(dataDir);
  const p = spawn(process.env.PROXION_PYTHON || 'python', ['scripts/run_test_gateway.py'], {
    cwd: REPO,
    env: {
      ...process.env,
      PROXION_DATA_DIR: dataDir, PROXION_HTTP_PORT: String(httpPort), PROXION_WS_PORT: String(wsPort),
      PROXION_HOST: '127.0.0.1', PROXION_PUBLIC_URL: '', PROXION_REQUIRE_AUTH: '0',
      PROXION_CSS_URL: '', PROXION_CSS_EMAIL: '', PROXION_CSS_PASSWORD: '',
      PROXION_ALLOW_PRIVATE_RELAY: '1', PROXION_ALLOW_INSECURE_FEDERATION: '1', PROXION_WEB_DIR: WEB,
    },
  });
  procs.push(p);
  let log = '';
  p.stdout.on('data', d => { log += d; }); p.stderr.on('data', d => { log += d; });
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    if (p.exitCode !== null) break;
    if (/PROXION_GATEWAY_READY/.test(log) && await portUp(httpPort)) return `http://127.0.0.1:${httpPort}/`;
    await sleep(300);
  }
  throw new Error(`${name} gateway did not start:\n${log.slice(-800)}`);
}

async function openClient(browser, url, name) {
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  page.on('pageerror', e => console.error(`  [${name}] pageerror: ${e.message}`));
  await page.setViewport({ width: 1280, height: 800, deviceScaleFactor: 1 });
  await page.evaluateOnNewDocument((n) => {
    try { localStorage.setItem('proxion_display_name', n); localStorage.setItem('proxion_pod_setup_skipped', '1'); } catch { /* storage off */ }
  }, name);
  await page.goto(url, { waitUntil: 'load', timeout: 20000 });
  await page.waitForFunction(() => document.querySelector('.dot')?.classList.contains('online'), { timeout: 20000 });
  return page;
}

async function send(page, text) {
  await page.focus('#message-input');
  await page.evaluate((t) => {
    const i = document.getElementById('message-input');
    i.value = t; i.dispatchEvent(new Event('input', { bubbles: true }));
  }, text);
  await page.keyboard.press('Enter');
  await page.waitForFunction((t) => document.getElementById('message-feed')?.textContent.includes(t.split('\n')[0].replace(/[*`]/g, '')),
    { timeout: 8000 }, text);
}

// Freeze motion, hide transient chrome, and replace per-run values so the diff
// reflects layout and colour only.
const FREEZE_CSS =
  '*, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; }' +
  '#history-skeleton, #nat-warning-banner, #toast-container { display: none !important; }' +
  '#typing-indicator { visibility: hidden !important; }' +
  '.avatar-presence, .dot { animation: none !important; }';
async function normalize(page) {
  await page.evaluate(() => {
    const set = (sel, text) => document.querySelectorAll(sel).forEach((e) => { e.textContent = text; });
    set('time', '12:00');
    set('.id-suffix', '·0000');
    set('#settings-did, #settings-proxion-address, #my-address-short, #fingerprint-words, #profile-webid', '-');
    set('.date-divider, .day-divider', 'Today');
    set('.dm-item-ts', 'now');
    for (const e of document.querySelectorAll('.avatar.placeholder, .dm-avatar, .room-avatar, .member-avatar')) {
      if (e.style.background) e.style.background = 'hsl(210, 55%, var(--webid-l))';
    }
    for (const e of document.querySelectorAll('.msg-sender, .reply-author, [style*="hsl("]')) {
      if (e.style.color && e.style.color.startsWith('hsl')) e.style.color = 'hsl(210, 55%, var(--webid-l))';
      if (e.style.backgroundColor && e.style.backgroundColor.startsWith('hsl')) e.style.background = 'hsl(210, 55%, var(--webid-l))';
    }
    const dot = document.getElementById('settings-pod-status-dot');
    if (dot) { dot.textContent = 'status'; dot.style.color = ''; }
    document.activeElement?.blur?.();
  });
}

let failures = 0, written = 0;
async function capture(page, name) {
  for (const theme of THEMES) {
    await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: theme }]);
    await page.mouse.move(1279, 799);
    await normalize(page);
    await sleep(200);
    const shot = await page.screenshot();
    const file = `${theme}-${name}.png`;
    if (OUT_DIR) {
      mkdirSync(OUT_DIR, { recursive: true });
      writeFileSync(join(OUT_DIR, file), shot);
      console.log(`  [out] ${file}`);
      written++;
      continue;
    }
    const baselinePath = join(BASELINE_DIR, file);
    if (UPDATE || !existsSync(baselinePath)) {
      mkdirSync(BASELINE_DIR, { recursive: true });
      writeFileSync(baselinePath, shot);
      console.log(`  [baseline] wrote visual-baseline/${file}`);
      written++;
      continue;
    }
    const actual = PNG.sync.read(shot);
    const baseline = PNG.sync.read(readFileSync(baselinePath));
    if (actual.width !== baseline.width || actual.height !== baseline.height) {
      console.error(`  ✗ ${file}: size changed ${baseline.width}x${baseline.height} -> ${actual.width}x${actual.height}`);
      failures++;
      continue;
    }
    const diff = new PNG({ width: actual.width, height: actual.height });
    const mismatched = pixelmatch(actual.data, baseline.data, diff.data, actual.width, actual.height, { threshold: 0.1 });
    const frac = mismatched / (actual.width * actual.height);
    if (frac > TOLERANCE) {
      mkdirSync(ARTIFACT_DIR, { recursive: true });
      writeFileSync(join(ARTIFACT_DIR, `visual-${theme}-${name}-actual.png`), shot);
      writeFileSync(join(ARTIFACT_DIR, `visual-${theme}-${name}-diff.png`), PNG.sync.write(diff));
      console.error(`  ✗ ${file}: ${mismatched} px differ (${(frac * 100).toFixed(3)}%), see artifacts/visual-${theme}-${name}-{actual,diff}.png`);
      failures++;
    } else {
      console.log(`  ✓ ${file}: ${mismatched} px differ (${(frac * 100).toFixed(3)}%)`);
    }
  }
}

const shown = (page, id) => page.waitForFunction((i) => {
  const e = document.getElementById(i); return e && getComputedStyle(e).display !== 'none';
}, { timeout: 8000 }, id);

let browser = null;
let step = 'start';
try {
  const [urlA, urlB] = [await startGateway('alice'), await startGateway('bob')];
  browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--ignore-certificate-errors', '--no-sandbox', '--disable-gpu', '--force-color-profile=srgb', '--font-render-hinting=none'],
  });
  const alice = await openClient(browser, urlA, 'Alice');
  await alice.addStyleTag({ content: FREEZE_CSS });
  await sleep(1500);

  step = 'empty';
  await capture(alice, 'empty');

  step = 'settings';
  await alice.evaluate(() => document.getElementById('settings-btn').click());
  await shown(alice, 'settings-modal');
  await sleep(400);
  await capture(alice, 'settings');
  await alice.evaluate(() => document.getElementById('settings-save-btn').click());

  step = 'dialog';
  await alice.evaluate(() => document.getElementById('create-room-btn').click());
  await shown(alice, 'room-create-modal');
  await alice.type('#room-name-input', 'design-review');
  await capture(alice, 'dialog');

  step = 'room';
  await alice.evaluate(() => document.getElementById('room-create-submit').click());
  await shown(alice, 'room-invite-result');
  await alice.evaluate(() => document.getElementById('room-create-done-btn').click());
  await alice.waitForFunction(() => /design-review/.test(document.getElementById('chat-header-name')?.textContent || ''), { timeout: 8000 });
  await send(alice, 'Morning. The new type scale is in, take a look when you can.');
  await send(alice, 'Spacing now follows the **4px rhythm** and buttons share one `btn` base.');
  await send(alice, 'Checklist for today:\n- tokens\n- components\n- inline styles');
  await send(alice, 'Light and dark both pass the contrast audit.');
  await sleep(500);
  await capture(alice, 'room');

  step = 'members';
  await alice.evaluate(() => document.getElementById('members-toggle').click());
  await shown(alice, 'members-panel');
  await sleep(300);
  await capture(alice, 'room-members');
  await alice.evaluate(() => document.getElementById('members-panel-close').click());

  step = 'dm-handshake';
  const bob = await openClient(browser, urlB, 'Bob');
  await bob.evaluate(() => document.getElementById('settings-btn')?.click());
  const bobAddr = await bob.waitForFunction(
    () => window.proxionAddress || localStorage.getItem('proxion_my_address'), { timeout: 10000 }).then(h => h.jsonValue());
  await bob.evaluate(() => document.getElementById('settings-save-btn').click());
  await alice.evaluate(() => document.getElementById('add-peer-btn').click());
  await shown(alice, 'add-peer-modal');
  await alice.type('#add-peer-input', bobAddr);
  await alice.evaluate(() => document.getElementById('add-peer-submit-btn').click());
  await bob.waitForFunction(() => document.querySelector('#friend-request-list li [data-fr-action="accept"]'), { timeout: 20000 });
  await bob.evaluate(() => document.querySelector('#friend-request-list li [data-fr-action="accept"]').click());
  await bob.waitForFunction(() => document.querySelector('#dm-list li, #contacts-list li'), { timeout: 20000 });
  await sleep(500);
  await bob.evaluate(() => document.querySelector('#dm-list li, #contacts-list li').click());
  await alice.waitForFunction(() => document.querySelector('#dm-list li, #contacts-list li'), { timeout: 20000 });
  await sleep(500);
  await alice.evaluate(() => document.querySelector('#dm-list li, #contacts-list li').click());
  await alice.waitForFunction(() => /bob/i.test(document.getElementById('chat-header-name')?.textContent || ''), { timeout: 8000 });

  step = 'dm';
  await sleep(500);
  await send(alice, 'Hi Bob, did the invite link work for you?');
  await bob.waitForFunction(() => document.getElementById('message-feed')?.textContent.includes('did the invite link work'), { timeout: 15000 });
  await send(bob, 'It did. Opened it and the room showed up right away.');
  await alice.waitForFunction(() => document.getElementById('message-feed')?.textContent.includes('showed up right away'), { timeout: 15000 });
  await sleep(800);
  await capture(alice, 'dm');
} catch (e) {
  console.error(`  ✗ [${step}] ${e.message.split('\n')[0]}`);
  failures++;
} finally {
  if (browser) await browser.close();
  for (const p of procs) { try { p.kill(); } catch { /* gone */ } }
  await sleep(400);
  for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch { /* locked */ } }
}

if (written && !OUT_DIR) console.log(`\nBaselines written: ${written}. Re-run without --update to verify.`);
if (failures) { console.error(`\n✗ Visual regression: ${failures} screen(s) drifted or failed. If intended, re-run with --update.`); process.exitCode = 1; }
else if (!written) console.log('\n✓ No visual regressions.');
