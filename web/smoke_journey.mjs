// User-journey smoke (ROADMAP_2 H2) — drives a real UI flow with assertions, not
// just "the page evaluated without errors" (that's smoke_browser).
//
// Self-contained: it spawns its OWN isolated gateway (a fresh PROXION_DATA_DIR, free
// ports, auth off) so the journey is deterministic and independent of the developer's
// persistent rooms/identity. It then connects a real browser, creates a room, opens
// it, sends a message, and asserts the message renders — failing on any page error.
//
//   node web/smoke_journey.mjs          (spawns its own gateway; needs python + chrome)
//   PROXION_SMOKE_URL=https://localhost:8080/ node web/smoke_journey.mjs  (use a running one)
//
// Exit 0 = journey completed with all assertions met, non-zero = a step failed.

import { existsSync } from 'fs';
import { spawn } from 'child_process';
import { createServer, connect } from 'net';
import { mkdtempSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join, resolve } from 'path';
import puppeteer from 'puppeteer-core';

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
const ROOM = 'journey-' + Math.random().toString(36).slice(2, 7);
const MSG = 'hello-' + Math.random().toString(36).slice(2, 7);

function freePort() {
  return new Promise((res, rej) => {
    const s = createServer();
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
    s.on('error', rej);
  });
}

// ── Spawn an isolated gateway unless an external URL is provided ──
let gw = null, dataDir = null, URL = process.env.PROXION_SMOKE_URL;
async function startGateway() {
  const httpPort = await freePort();
  const wsPort = await freePort();
  dataDir = mkdtempSync(join(tmpdir(), 'proxion-journey-'));
  const env = {
    ...process.env,
    PROXION_DATA_DIR: dataDir,
    PROXION_HTTP_PORT: String(httpPort),
    PROXION_WS_PORT: String(wsPort),
    PROXION_REQUIRE_AUTH: '0',                 // loopback test: skip the challenge handshake
    // Force PUBLIC_URL empty: it's returned verbatim as the WS url, but the WS server
    // is on wsPort (not httpPort), so a value points the browser at the wrong port.
    // Empty → the gateway derives wss://host:wsPort. (Must be set empty rather than
    // unset, so dotenv's override=False doesn't leak the dev .env's PUBLIC_URL in.)
    PROXION_PUBLIC_URL: '',
    PROXION_WEB_DIR: resolve(REPO, 'web'),
    // Run pod-less (local rooms only): fast startup AND isolation — a test must
    // never touch the developer's real Solid Pod. (dotenv load is override=False,
    // so these empty values win over any .env CSS config.)
    PROXION_CSS_URL: '', PROXION_CSS_EMAIL: '', PROXION_CSS_PASSWORD: '',
  };
  gw = spawn('python', ['run_gateway.py'], { cwd: REPO, env });
  let log = '';
  gw.stdout.on('data', d => { log += d; });
  gw.stderr.on('data', d => { log += d; });
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (/PROXION_GATEWAY_READY/.test(log)) break;
    if (gw.exitCode !== null) { throw new Error('gateway exited early:\n' + log.slice(-800)); }
    await new Promise(r => setTimeout(r, 300));
  }
  if (!/PROXION_GATEWAY_READY/.test(log)) throw new Error('gateway not ready in 30s:\n' + log.slice(-800));
  // READY is printed before the HTTP server actually binds — wait for the port to accept.
  await waitForPort(httpPort, 15000);
  URL = `https://127.0.0.1:${httpPort}/`;
}

function waitForPort(port, timeoutMs) {
  return new Promise((res, rej) => {
    const deadline = Date.now() + timeoutMs;
    const tryOnce = () => {
      const c = connect(port, '127.0.0.1');
      c.once('connect', () => { c.destroy(); res(); });
      c.once('error', () => {
        c.destroy();
        if (Date.now() > deadline) rej(new Error(`port ${port} not listening in time`));
        else setTimeout(tryOnce, 300);
      });
    };
    tryOnce();
  });
}

let step = 'init';
const fatal = [];
const fail = (m) => { console.error(`  ✗ [${step}] ${m}`); process.exitCode = 1; };

let browser = null;
try {
  if (!URL) { step = 'spawn-gateway'; await startGateway(); }

  browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--ignore-certificate-errors', '--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage();
  page.on('pageerror', (e) => fatal.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') fatal.push('console.error: ' + m.text()); });

  step = 'load';
  await page.goto(URL, { waitUntil: 'load', timeout: 20000 });

  step = 'dismiss-onboarding';
  // Fresh data dir → first-run onboarding modal would intercept clicks. Skip it.
  await page.evaluate(() => {
    const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none';
    localStorage.setItem('proxion_pod_setup_skipped', '1');
  });

  step = 'connect';
  await page.waitForFunction(() => document.querySelector('.dot')?.classList.contains('online'),
    { timeout: 15000 }).catch(() => fail('gateway did not reach "online" within 15s'));

  step = 'create-room';
  await page.evaluate(() => document.getElementById('create-room-btn').click());
  await page.waitForFunction(() => {
    const m = document.getElementById('room-create-modal');
    return m && getComputedStyle(m).display !== 'none';
  }, { timeout: 5000 });
  await page.type('#room-name-input', ROOM);
  await page.evaluate(() => document.getElementById('room-create-submit').click());
  await page.waitForFunction((name) =>
    [...document.querySelectorAll('#room-list li')].some(li => (li.textContent || '').includes(name)),
    { timeout: 10000 }, ROOM).catch(() => fail(`room "${ROOM}" never appeared in #room-list`));

  step = 'open-room';
  await page.evaluate(() => {
    document.getElementById('room-create-done-btn')?.click();
    const m = document.getElementById('room-create-modal'); if (m) m.style.display = 'none';
  });
  await page.evaluate((name) => {
    [...document.querySelectorAll('#room-list li')].find(l => (l.textContent || '').includes(name))?.click();
  }, ROOM);

  step = 'send-message';
  await page.focus('#message-input');
  await page.type('#message-input', MSG);
  await page.keyboard.press('Enter');
  // Optimistic render should make it appear ~instantly; then the server echo dedups.
  await page.waitForFunction((text) => document.getElementById('message-feed')?.textContent.includes(text),
    { timeout: 10000 }, MSG).catch(() => fail(`sent message "${MSG}" never rendered in the feed`));
  // Wait for the echo to settle, then assert EXACTLY ONE copy (optimistic + echo must dedup).
  await new Promise(r => setTimeout(r, 1500));
  const copies = await page.evaluate((text) =>
    [...document.querySelectorAll('#message-feed .message')].filter(el => (el.textContent || '').includes(text)).length, MSG);
  if (copies !== 1) fail(`expected exactly 1 rendered copy of "${MSG}", found ${copies} (optimistic/echo dedup failed)`);
  const stillPending = await page.evaluate((text) =>
    [...document.querySelectorAll('#message-feed .message.msg-pending')].some(el => (el.textContent || '').includes(text)), MSG);
  if (stillPending) fail(`message "${MSG}" stuck in pending state — echo never cleared .msg-pending`);

  step = 'done';
  if (fatal.length) { console.error('  page/console errors:\n   - ' + fatal.join('\n   - ')); process.exitCode = 1; }
  if (!process.exitCode) {
    console.log(`  ✓ journey OK — connected, created room "${ROOM}", sent + rendered "${MSG}", no page errors.`);
  }
} catch (e) {
  console.error(`  ✗ [${step}] threw: ${e.message}`);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  if (gw) { try { gw.kill(); } catch {} }
  if (dataDir) { try { rmSync(dataDir, { recursive: true, force: true }); } catch {} }
}
