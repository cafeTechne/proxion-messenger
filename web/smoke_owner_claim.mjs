// Gateway owner claim smoke: a gateway that requires sign-in starts unclaimed
// and prints a one-time claim link. Opening it in a browser signs in and claims
// in one go; the code leaves the address bar, the owner list shows in Settings,
// the claim survives a reload, a stranger sees the banner before the claim and
// not after, and a second visit with the same link does not claim.
//
//   node web/smoke_owner_claim.mjs      (spawns its own gateway; needs python + chrome)
//
// Exit 0 = every assertion met.

import { existsSync, mkdtempSync, rmSync } from 'fs';
import { spawn } from 'child_process';
import { createServer, connect } from 'net';
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
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function freePort() {
  return new Promise((res, rej) => {
    const s = createServer();
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
    s.on('error', rej);
  });
}
function waitForPort(port, timeoutMs) {
  return new Promise((res, rej) => {
    const deadline = Date.now() + timeoutMs;
    const tryOnce = () => {
      const c = connect(port, '127.0.0.1');
      c.once('connect', () => { c.destroy(); res(); });
      c.once('error', () => { c.destroy(); Date.now() > deadline ? rej(new Error(`port ${port} idle`)) : setTimeout(tryOnce, 300); });
    };
    tryOnce();
  });
}

let gw = null, dataDir = null, log = '';
async function startGateway() {
  const httpPort = await freePort();
  const wsPort = await freePort();
  dataDir = mkdtempSync(join(tmpdir(), 'proxion-claim-'));
  const env = {
    ...process.env,
    PROXION_DATA_DIR: dataDir, PROXION_HOST: '127.0.0.1',
    PROXION_HTTP_PORT: String(httpPort), PROXION_WS_PORT: String(wsPort),
    // Sign-in required by configuration, as behind a reverse proxy.
    PROXION_REQUIRE_AUTH: '1', PROXION_PUBLIC_URL: '', PROXION_ADMIN_API_TOKEN: '',
    PROXION_WEB_DIR: resolve(REPO, 'web'),
    PROXION_CSS_URL: '', PROXION_CSS_EMAIL: '', PROXION_CSS_PASSWORD: '',
  };
  gw = spawn(process.env.PROXION_PYTHON || 'python', ['run_gateway.py'], { cwd: REPO, env });
  gw.stdout.on('data', d => { log += d; });
  gw.stderr.on('data', d => { log += d; });
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline && !/Claim\s+: \S+/.test(log)) {
    if (gw.exitCode !== null) throw new Error('gateway exited early:\n' + log.slice(-800));
    await sleep(300);
  }
  const m = /Claim\s+: (\S+)/.exec(log);
  if (!m) throw new Error('gateway printed no claim link:\n' + log.slice(-800));
  await waitForPort(httpPort, 15000);
  return { base: `https://127.0.0.1:${httpPort}/`, claimUrl: m[1] };
}

let step = 'init';
const fail = (m) => { console.error(`  ✗ [${step}] ${m}`); process.exitCode = 1; };
const online = (page) => page.waitForFunction(
  () => document.querySelector('.dot')?.classList.contains('online'), { timeout: 20000 });
const bannerShown = (page) => page.evaluate(
  () => document.getElementById('owner-claim-banner')?.classList.contains('visible') === true);
const ownerSection = (page) => page.evaluate(
  () => document.getElementById('settings-owner-devices')?.hidden === false);

async function open(browser, url) {
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(e.message));
  await page.goto(url, { waitUntil: 'load', timeout: 20000 });
  await page.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  return { ctx, page, errs };
}

let browser = null;
try {
  step = 'spawn-gateway';
  const { base, claimUrl } = await startGateway();
  if (!claimUrl.includes('#claim=')) fail(`claim link has no code: ${claimUrl}`);
  const code = claimUrl.split('#claim=')[1];

  browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--ignore-certificate-errors', '--no-sandbox', '--disable-gpu'],
  });

  step = 'stranger-before';
  const stranger = await open(browser, base);
  await online(stranger.page).catch(() => fail('stranger never went online'));
  await stranger.page.waitForFunction(
    () => document.getElementById('owner-claim-banner')?.classList.contains('visible'),
    { timeout: 10000 }).catch(() => fail('unclaimed gateway shows no claim banner'));

  step = 'claim-link';
  const owner = await open(browser, claimUrl);
  if (await owner.page.evaluate(() => location.hash) !== '') fail('claim code still in the address bar');
  await online(owner.page).catch(() => fail('owner never went online'));
  await owner.page.waitForFunction(
    () => document.getElementById('settings-owner-devices')?.hidden === false,
    { timeout: 15000 }).catch(() => fail('claim did not make this browser the owner'));
  if (await bannerShown(owner.page)) fail('owner still sees the claim banner');
  if (!await owner.page.evaluate(() => !!localStorage.getItem('proxion_gateway_delegation_cert'))) {
    fail('owner got no gateway delegation cert');
  }
  const history = await owner.page.evaluate(() => location.href);
  if (history.includes(code)) fail('claim code left in the page URL');

  step = 'owner-reload';
  await owner.page.reload({ waitUntil: 'load' });
  await online(owner.page).catch(() => fail('owner never came back online'));
  await owner.page.waitForFunction(
    () => document.getElementById('settings-owner-devices')?.hidden === false,
    { timeout: 15000 }).catch(() => fail('owner lost ownership after a reload'));

  step = 'owner-list';
  await owner.page.evaluate(() => document.getElementById('settings-btn')?.click());
  await owner.page.waitForFunction(
    () => (document.getElementById('settings-owner-devices-list')?.children.length || 0) === 1,
    { timeout: 10000 }).catch(() => fail('Settings does not list exactly one owner'));

  step = 'stranger-after';
  await stranger.page.reload({ waitUntil: 'load' });
  await online(stranger.page).catch(() => fail('stranger never came back online'));
  await sleep(1000);
  if (await bannerShown(stranger.page)) fail('claimed gateway still offers the claim banner');
  if (await ownerSection(stranger.page)) fail('stranger sees the owner list');

  step = 'replay';
  const replay = await open(browser, claimUrl);
  await online(replay.page).catch(() => fail('replay browser never went online'));
  await sleep(1500);
  if (await ownerSection(replay.page)) fail('a second visit with the same link claimed the gateway');

  const errs = [...stranger.errs, ...owner.errs, ...replay.errs];
  if (errs.length) fail('page errors: ' + errs.join(' | '));
  if (log.split('#claim=').length - 1 < 1) fail('claim link not in the gateway log');
  if (!process.exitCode) console.log('  ✓ owner claim: link claims once, persists, banner and list gated');
} catch (e) {
  fail(e && e.stack || String(e));
} finally {
  try { await browser?.close(); } catch { /* closing */ }
  try { gw?.kill(); } catch { /* gone */ }
  await sleep(300);
  try { if (dataDir) rmSync(dataDir, { recursive: true, force: true }); } catch { /* locked */ }
}
