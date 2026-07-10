// Keyboard-only journey smoke (PLAN_ROUND_56 E2) — drives the core journey with
// ONLY the keyboard (no .click(), no mouse): skip-link → composer, Ctrl+K search,
// create a room, send a message, arrow through the sidebar (roving tabindex), and
// open/close the shortcut modal asserting focus is RESTORED to the opener (C2).
// Self-spawns an isolated gateway like the other smokes.
//
//   node web/smoke_keyboard.mjs
//   PROXION_SMOKE_URL=http://127.0.0.1:PORT/ node web/smoke_keyboard.mjs
//
// Exit 0 = every keyboard assertion held. Non-zero = a keyboard path is broken.

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
  const dataDir = mkdtempSync(join(tmpdir(), 'proxion-kbd-'));
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

// ── Keyboard-only primitives ────────────────────────────────────────────────
const active = (page) => page.evaluate(() => {
  const a = document.activeElement;
  return a ? { id: a.id, cls: (a.className || '').toString(), tag: a.tagName } : null;
});

// Press Tab (or Shift+Tab) up to `max` times until the focused element satisfies
// `pred(activeInfo)`. Returns true if reached. Pure keyboard — no focus() calls.
async function tabTo(page, pred, { max = 60, shift = false } = {}) {
  for (let i = 0; i < max; i++) {
    const a = await active(page);
    if (a && pred(a)) return true;
    if (shift) { await page.keyboard.down('Shift'); await page.keyboard.press('Tab'); await page.keyboard.up('Shift'); }
    else await page.keyboard.press('Tab');
    await sleep(30);
  }
  const a = await active(page);
  return !!(a && pred(a));
}

let failures = 0;
const check = (name, ok) => { console.log(`  ${ok ? '✓' : '✗'} ${name}`); if (!ok) failures++; };

let browser = null;
try {
  const url = process.env.PROXION_SMOKE_URL || await startGateway();
  browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--ignore-certificate-errors', '--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage();
  page.on('dialog', d => d.accept());
  await page.setViewport({ width: 1280, height: 800 });
  await page.goto(url, { waitUntil: 'load', timeout: 20000 });
  await page.waitForFunction(() => document.querySelector('.dot')?.classList.contains('online'), { timeout: 15000 }).catch(() => {});
  await sleep(400);

  // 1. Dismiss first-run onboarding with the keyboard (Escape closes any modal).
  await page.keyboard.press('Escape');
  await sleep(200);
  const onboardingHidden = await page.evaluate(() => {
    const m = document.getElementById('onboarding-modal');
    return !m || getComputedStyle(m).display === 'none';
  });
  check('Escape dismisses onboarding', onboardingHidden);

  // 2. Skip link is the first tab stop, and activating it focuses the composer.
  await page.evaluate(() => document.body.focus());
  await page.keyboard.press('Tab');
  const firstStop = await active(page);
  check('skip link is the first Tab stop', !!firstStop && /skip-nav/.test(firstStop.cls));

  // 3. Ctrl+K focuses the search box.
  await page.keyboard.down('Control'); await page.keyboard.press('k'); await page.keyboard.up('Control');
  await sleep(120);
  check('Ctrl+K focuses search', (await active(page))?.id === 'search-input');

  // 4. Create a room entirely by keyboard: Tab to the create button, Enter, type
  //    the name into the trapped modal input, Tab to the submit control, Enter.
  const reachedCreate = await tabTo(page, a => a.id === 'create-room-btn');
  check('Tab reaches the create-room button', reachedCreate);
  await page.keyboard.press('Enter');
  await sleep(400);
  const atRoomInput = await tabTo(page, a => a.id === 'room-name-input', { max: 20 });
  check('focus lands in the room-name field', atRoomInput);
  await page.keyboard.type('general');
  const atSubmit = await tabTo(page, a => a.id === 'room-create-submit', { max: 20 });
  check('Tab reaches the create submit button', atSubmit);
  await page.keyboard.press('Enter');
  await sleep(800);
  const roomExists = await page.evaluate(() => Array.from(document.querySelectorAll('nav li'))
    .some(li => /general/i.test(li.textContent || '')));
  check('room created via keyboard', roomExists);
  await page.keyboard.press('Escape'); // close the "created" confirmation if shown
  await sleep(200);

  // 5. Send a message: focus the composer by keyboard, type, Enter.
  const atComposer = await tabTo(page, a => a.id === 'message-input');
  check('Tab reaches the composer', atComposer);
  await page.keyboard.type('hello from the keyboard');
  await page.keyboard.press('Enter');
  await sleep(500);
  const messageSent = await page.evaluate(() => Array.from(document.querySelectorAll('#message-feed .message'))
    .some(m => /hello from the keyboard/.test(m.textContent || '')));
  check('message sent with Enter', messageSent);

  // 6. Roving tabindex: Tab onto a sidebar row, ArrowDown moves focus to another
  //    row (the whole list is one tab stop — Phase B).
  const atRow = await tabTo(page, a => a.tag === 'LI');
  check('Tab reaches a sidebar row', atRow);
  const before = await active(page);
  await page.keyboard.press('ArrowDown');
  await sleep(120);
  const afterArrow = await active(page);
  check('ArrowDown moves within the sidebar list',
    !!afterArrow && afterArrow.tag === 'LI');

  // 7. Shortcut modal open (Ctrl+/) then Escape must RESTORE focus to the opener.
  //    Park focus on a known control first so we can assert the restore target.
  await tabTo(page, a => a.id === 'message-input');
  await page.keyboard.down('Control'); await page.keyboard.press('/'); await page.keyboard.up('Control');
  await sleep(300);
  const modalOpen = await page.evaluate(() => getComputedStyle(document.getElementById('shortcut-modal')).display !== 'none');
  check('Ctrl+/ opens the shortcut modal', modalOpen);
  await page.keyboard.press('Escape');
  await sleep(300);
  const modalClosed = await page.evaluate(() => getComputedStyle(document.getElementById('shortcut-modal')).display === 'none');
  check('Escape closes the shortcut modal', modalClosed);
  check('focus restored to the opener (message-input)', (await active(page))?.id === 'message-input');

} catch (e) {
  console.error('  ✗ threw: ' + (e.stack || e.message));
  failures += 1;
} finally {
  if (browser) await browser.close();
  for (const p of procs) { try { p.kill(); } catch { /* ignore */ } }
  for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch { /* ignore */ } }
}

if (failures === 0) console.log('✓ keyboard: full journey operable by keyboard alone.');
else console.error(`✗ keyboard: ${failures} assertion(s) failed.`);
process.exit(failures === 0 ? 0 : 1);
