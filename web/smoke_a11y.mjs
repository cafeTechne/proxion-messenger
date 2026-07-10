// Accessibility smoke (PLAN_ROUND_56 E1) — runs axe-core (WCAG 2.2 AA rule set)
// against the real running app across every major screen, failing on any
// violation. Self-spawns an isolated gateway (fresh data dir, auth off, pod-less)
// like the other smokes, and injects axe from node_modules (local file — no CDN,
// CSP-safe).
//
//   node web/smoke_a11y.mjs            (spawns its own gateway; needs python + chrome)
//   PROXION_SMOKE_URL=http://127.0.0.1:PORT/ node web/smoke_a11y.mjs
//
// Exit 0 = zero violations across all screens. Non-zero = at least one.

import { existsSync, mkdtempSync, rmSync, readFileSync } from 'fs';
import { spawn } from 'child_process';
import { createServer, connect } from 'net';
import { tmpdir } from 'os';
import { join, resolve } from 'path';
import puppeteer from 'puppeteer-core';

// Hard timeout — a stray native dialog or hung gateway must never wedge CI.
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
const AXE_PATH = resolve(WEB, 'node_modules', 'axe-core', 'axe.min.js');
if (!existsSync(AXE_PATH)) { console.error('axe-core not installed (npm i -D axe-core)'); process.exit(2); }
// Read the axe source once; it's injected via page.evaluate (Runtime.evaluate,
// which runs in the debugger context and is NOT subject to the app's strict CSP,
// unlike an injected <script> tag which the CSP blocks).
const AXE_SRC = readFileSync(AXE_PATH, 'utf8');

// WCAG 2.2 AA rule tags. best-practice is intentionally excluded from the gate
// (opinionated rules like region/heading-order are reported as warnings only).
const AA_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

// Rules we knowingly accept (each MUST carry a justification). Empty = strict.
const ALLOWLIST = {
  // (Phase D fixed the palette-token contrast failures; gate is now strict.)
};

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
  const dataDir = mkdtempSync(join(tmpdir(), 'proxion-a11y-'));
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

// Each screen: reach a settled UI state (async prepare in-page), then axe it.
// prepare receives no args and runs IN the page.
const SCREENS = [
  { name: 'welcome', prepare: () => { document.querySelectorAll('[role="dialog"],[id$="-modal"]').forEach(m => { m.style.display = 'none'; }); } },
  {
    name: 'room-with-messages',
    prepare: async () => {
      const $ = (id) => document.getElementById(id);
      $('create-room-btn').click();
      await new Promise(r => setTimeout(r, 250));
      $('room-name-input').value = 'general';
      $('room-create-submit').click();
      await new Promise(r => setTimeout(r, 700));
      $('room-create-done-btn') && $('room-create-done-btn').click();
      for (const t of ['hello there', 'a second message with `code`']) {
        const i = $('message-input'); i.value = t; i.dispatchEvent(new Event('input', { bubbles: true }));
        $('message-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        await new Promise(r => setTimeout(r, 200));
      }
    },
  },
  { name: 'settings', prepare: async () => { document.getElementById('settings-btn').click(); await new Promise(r => setTimeout(r, 300)); document.getElementById('settings-advanced-toggle')?.click(); await new Promise(r => setTimeout(r, 200)); } },
  { name: 'members-panel', prepare: async () => { document.querySelectorAll('[role="dialog"],[id$="-modal"]').forEach(m => { m.style.display = 'none'; }); const b = document.getElementById('members-toggle'); b.style.display = 'inline-block'; b.click(); await new Promise(r => setTimeout(r, 300)); } },
  { name: 'emoji-picker', prepare: async () => { document.querySelectorAll('[role="dialog"],[id$="-modal"]').forEach(m => { m.style.display = 'none'; }); const p = document.getElementById('emoji-picker'); if (p) p.style.display = 'block'; await new Promise(r => setTimeout(r, 150)); } },
  { name: 'shortcut-modal', prepare: async () => { const m = document.getElementById('shortcut-modal'); if (m) m.style.display = 'flex'; await new Promise(r => setTimeout(r, 150)); } },
  { name: 'onboarding', prepare: async () => { document.querySelectorAll('[id$="-modal"]').forEach(m => { if (m.id !== 'onboarding-modal') m.style.display = 'none'; }); const o = document.getElementById('onboarding-modal'); if (o) o.style.display = 'flex'; await new Promise(r => setTimeout(r, 150)); } },
];

let totalViolations = 0;
let browser = null;
try {
  const url = process.env.PROXION_SMOKE_URL || await startGateway();
  browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--ignore-certificate-errors', '--no-sandbox', '--disable-gpu'],
  });

  for (const screen of SCREENS) {
    const page = await browser.newPage();
    page.on('dialog', d => d.accept());
    await page.setViewport({ width: 1280, height: 800 });
    await page.goto(url, { waitUntil: 'load', timeout: 20000 });
    // Dismiss the first-run onboarding for non-onboarding screens.
    if (screen.name !== 'onboarding') {
      await page.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
    }
    await page.waitForFunction(() => document.querySelector('.dot')?.classList.contains('online'), { timeout: 15000 }).catch(() => {});
    await sleep(300);
    if (screen.prepare) { await page.evaluate(screen.prepare); await sleep(300); }

    await page.evaluate(AXE_SRC);   // defines window.axe (CSP-bypassing, see above)
    const results = await page.evaluate(async (tags) => {
      // eslint-disable-next-line no-undef
      return await axe.run(document, { runOnly: { type: 'tag', values: tags } });
    }, AA_TAGS);
    await page.close();

    const violations = results.violations.filter(v => !ALLOWLIST[v.id]);
    if (violations.length === 0) {
      console.log(`  ✓ ${screen.name}: no violations`);
    } else {
      totalViolations += violations.length;
      console.error(`  ✗ ${screen.name}: ${violations.length} violation(s)`);
      for (const v of violations) {
        console.error(`      [${v.impact}] ${v.id}: ${v.help} (${v.nodes.length} node(s))`);
        for (const n of v.nodes.slice(0, 3)) console.error(`         ${n.target.join(' ')} — ${(n.failureSummary || '').split('\n').slice(0, 2).join(' | ')}`);
      }
    }
  }
} catch (e) {
  console.error('  ✗ threw: ' + e.message);
  totalViolations += 1;
} finally {
  if (browser) await browser.close();
  for (const p of procs) { try { p.kill(); } catch { /* ignore */ } }
  for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch { /* ignore */ } }
}

if (totalViolations === 0) console.log('✓ axe: no WCAG 2.2 AA violations.');
else console.error(`✗ axe: ${totalViolations} violation(s) across screens.`);
process.exit(totalViolations === 0 ? 0 : 1);
