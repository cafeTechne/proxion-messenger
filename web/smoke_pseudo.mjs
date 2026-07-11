// Pseudo-locale + RTL smoke (PLAN_ROUND_56 PL1 + I3) — boots the app under the
// generated pseudo-locale (qps: accented + padded + ⟦⟧-bracketed) and under a
// real RTL locale (ar), asserting:
//   PL1  (qps)  no known raw-English sentinel is still visible (catches
//               un-externalized strings), no horizontal overflow from the +40%
//               padded strings (catches i18n-hostile fixed widths), 0 errors.
//   I3   (ar)   <html dir=rtl>, no horizontal overflow, and the members drawer
//               opens from the correct (inline-start = left) side.
// Self-spawns an isolated gateway like the other smokes.
//
//   node web/smoke_pseudo.mjs
//   PROXION_SMOKE_URL=http://127.0.0.1:PORT/ node web/smoke_pseudo.mjs

import { existsSync, mkdtempSync, rmSync } from 'fs';
import { spawn } from 'child_process';
import { createServer, connect } from 'net';
import { tmpdir } from 'os';
import { join, resolve } from 'path';
import puppeteer from 'puppeteer-core';

setTimeout(() => { console.error('HARD TIMEOUT'); process.exit(3); }, 150000);

const CHROME = [
  process.env.PROXION_CHROME,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
].filter(Boolean).find(p => p && existsSync(p));
if (!CHROME) { console.error('No Chrome/Edge found; set PROXION_CHROME.'); process.exit(2); }

const REPO = resolve(process.cwd(), '..');
const WEB = resolve(REPO, 'web');

const freePort = () => new Promise((res, rej) => {
  const s = createServer();
  s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
  s.on('error', rej);
});
const waitForPort = (port, ms) => new Promise((res, rej) => {
  const deadline = Date.now() + ms;
  const tryOnce = () => {
    const c = connect(port, '127.0.0.1');
    c.once('connect', () => { c.destroy(); res(); });
    c.once('error', () => { c.destroy(); Date.now() > deadline ? rej(new Error(`port ${port} not up`)) : setTimeout(tryOnce, 250); });
  };
  tryOnce();
});
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const procs = [], dirs = [];
async function startGateway() {
  const httpPort = await freePort(), wsPort = await freePort();
  const dataDir = mkdtempSync(join(tmpdir(), 'proxion-pseudo-'));
  dirs.push(dataDir);
  const env = {
    ...process.env,
    PROXION_DATA_DIR: dataDir, PROXION_HTTP_PORT: String(httpPort), PROXION_WS_PORT: String(wsPort),
    PROXION_HOST: '127.0.0.1', PROXION_PUBLIC_URL: '', PROXION_REQUIRE_AUTH: '0',
    PROXION_CSS_URL: '', PROXION_CSS_EMAIL: '', PROXION_CSS_PASSWORD: '',
    PROXION_WEB_DIR: WEB, PROXION_LOG_LEVEL: 'WARNING',
  };
  const p = spawn('python', ['scripts/run_test_gateway.py'], { cwd: REPO, env });
  procs.push(p);
  let log = '';
  p.stdout.on('data', d => log += d); p.stderr.on('data', d => log += d);
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline && !/PROXION_GATEWAY_READY/.test(log)) {
    if (p.exitCode !== null) throw new Error('gateway exited:\n' + log.slice(-500));
    await sleep(300);
  }
  await waitForPort(httpPort, 15000);
  return `http://127.0.0.1:${httpPort}/`;
}

// Raw-English sentinels that ARE externalized: under qps they must render as
// pseudo (accented/bracketed), so their exact English must NOT be visible. A
// match means a string escaped the i18n layer.
const SENTINELS = ['Rooms', 'Voice Channels', 'Contacts', 'Friend Requests', 'Keyboard Shortcuts', 'No rooms yet.', 'Create a room'];

let failures = 0;
const check = (name, ok, extra = '') => { console.log(`  ${ok ? '✓' : '✗'} ${name}${extra ? ' — ' + extra : ''}`); if (!ok) failures++; };

async function loadWithLocale(browser, url, locale) {
  const page = await browser.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(e.message));
  page.on('dialog', d => d.accept());
  await page.setViewport({ width: 1280, height: 800 });
  await page.goto(url, { waitUntil: 'load', timeout: 20000 });
  await page.evaluate((loc) => localStorage.setItem('proxion_locale', loc), locale);
  await page.reload({ waitUntil: 'load', timeout: 20000 });
  await page.waitForFunction(() => document.querySelector('.dot')?.classList.contains('online'), { timeout: 15000 }).catch(() => {});
  await sleep(500);
  await page.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  await sleep(200);
  return { page, errs };
}

let browser = null;
try {
  const url = process.env.PROXION_SMOKE_URL || await startGateway();
  browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--ignore-certificate-errors', '--no-sandbox', '--disable-gpu'],
  });

  // ── PL1: pseudo-locale pass ──────────────────────────────────────────────
  console.log('qps (pseudo-locale):');
  {
    const { page, errs } = await loadWithLocale(browser, url, 'qps');
    const lang = await page.evaluate(() => document.documentElement.lang);
    check('locale applied (lang=qps)', lang === 'qps', `lang=${lang}`);

    const visible = await page.evaluate(() => document.body.innerText);
    const leaked = SENTINELS.filter(s => visible.includes(s));
    check('no raw-English sentinels leaked', leaked.length === 0, leaked.join(', '));

    // Padded strings must not force a horizontal scrollbar (desktop + narrow).
    for (const w of [1280, 360]) {
      await page.setViewport({ width: w, height: 800 });
      await sleep(200);
      const of = await page.evaluate(() => ({ s: document.documentElement.scrollWidth, c: document.documentElement.clientWidth }));
      check(`no horizontal overflow @${w}px`, of.s <= of.c + 1, `${of.s}>${of.c}`);
    }
    check('no page errors', errs.length === 0, errs.slice(0, 2).join(' | '));
    await page.close();
  }

  // ── I3: RTL pass (ar) ────────────────────────────────────────────────────
  console.log('ar (RTL):');
  {
    const { page, errs } = await loadWithLocale(browser, url, 'ar');
    const dir = await page.evaluate(() => document.documentElement.dir);
    check('document dir=rtl', dir === 'rtl', `dir=${dir}`);

    const of = await page.evaluate(() => ({ s: document.documentElement.scrollWidth, c: document.documentElement.clientWidth }));
    check('no horizontal overflow', of.s <= of.c + 1, `${of.s}>${of.c}`);

    // Members drawer must open from the inline-start (left, in RTL) side.
    await page.setViewport({ width: 360, height: 800 });
    await sleep(200);
    const geo = await page.evaluate(() => {
      const el = document.getElementById('members-panel');
      if (!el) return null;
      el.classList.add('mobile-open');
      const r = el.getBoundingClientRect();
      return { left: Math.round(r.left), right: Math.round(r.right), vw: window.innerWidth };
    });
    // Open ⇒ anchored at the left edge (inline-start in RTL) AND on-screen
    // (right edge past 0). left=-220 would mean it's still parked off-screen.
    check('members drawer opens from the left in RTL',
      !!geo && geo.left <= 1 && geo.right > 1 && geo.left > -20,
      geo ? `left=${geo.left} right=${geo.right}` : 'no drawer');
    check('no page errors (RTL)', errs.length === 0, errs.slice(0, 2).join(' | '));
    await page.close();
  }
} catch (e) {
  console.error('  ✗ threw: ' + (e.stack || e.message));
  failures += 1;
} finally {
  if (browser) await browser.close();
  for (const p of procs) { try { p.kill(); } catch { /* ignore */ } }
  for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch { /* ignore */ } }
}

if (failures === 0) console.log('✓ pseudo/RTL: all locale-robustness checks passed.');
else console.error(`✗ pseudo/RTL: ${failures} check(s) failed.`);
process.exit(failures === 0 ? 0 : 1);
