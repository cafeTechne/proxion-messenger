// Multi-device pairing smoke (slice 6) — drives the FULL delegation pairing flow
// across two isolated browser contexts against one gateway, with assertions.
//
// primary: Link a device -> pairing code.  new device: enter code -> submit ->
// primary approves (signs a delegation cert) -> new device stores it + reloads
// -> reconnects AS the account.  Asserts: safety codes match, the new device
// holds a cert for the primary's account, and the primary's Linked Devices list
// then shows the newly-registered device (proving it authenticated as the
// account via the delegation path).
//
//   node web/smoke_pairing.mjs      (spawns its own gateway; needs python + chrome)
//
// Exit 0 = pairing completed and all assertions met.

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

let gw = null, dataDir = null, URL = process.env.PROXION_SMOKE_URL;
async function startGateway() {
  const httpPort = await freePort();
  const wsPort = await freePort();
  dataDir = mkdtempSync(join(tmpdir(), 'proxion-pairing-'));
  const env = {
    ...process.env,
    PROXION_DATA_DIR: dataDir,
    PROXION_HTTP_PORT: String(httpPort), PROXION_WS_PORT: String(wsPort),
    PROXION_REQUIRE_AUTH: '0', PROXION_PUBLIC_URL: '',
    PROXION_WEB_DIR: resolve(REPO, 'web'),
    PROXION_CSS_URL: '', PROXION_CSS_EMAIL: '', PROXION_CSS_PASSWORD: '',
  };
  gw = spawn('python', ['run_gateway.py'], { cwd: REPO, env });
  let log = '';
  gw.stdout.on('data', d => { log += d; });
  gw.stderr.on('data', d => { log += d; });
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (/PROXION_GATEWAY_READY/.test(log)) break;
    if (gw.exitCode !== null) throw new Error('gateway exited early:\n' + log.slice(-800));
    await new Promise(r => setTimeout(r, 300));
  }
  if (!/PROXION_GATEWAY_READY/.test(log)) throw new Error('gateway not ready:\n' + log.slice(-800));
  await waitForPort(httpPort, 15000);
  URL = `https://127.0.0.1:${httpPort}/`;
}

let step = 'init';
const fail = (m) => { console.error(`  ✗ [${step}] ${m}`); process.exitCode = 1; };
const online = (page) => page.waitForFunction(
  () => document.querySelector('.dot')?.classList.contains('online'), { timeout: 15000 });

let browser = null;
try {
  if (!URL) { step = 'spawn-gateway'; await startGateway(); }
  browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--ignore-certificate-errors', '--no-sandbox', '--disable-gpu'],
  });

  // ── Primary device ──
  step = 'primary-load';
  const ctxP = await browser.createBrowserContext();
  const pageP = await ctxP.newPage();
  const perr = [];
  pageP.on('pageerror', e => perr.push('primary pageerror: ' + e.message));
  await pageP.goto(URL, { waitUntil: 'load', timeout: 20000 });
  await pageP.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  await online(pageP).catch(() => fail('primary never went online'));
  const primaryDid = await pageP.evaluate(() => localStorage.getItem('proxion_identity_did'));
  if (!primaryDid) fail('primary has no clientDid');

  step = 'primary-start-linking';
  await pageP.evaluate(() => document.getElementById('link-device-btn').click());
  await pageP.waitForFunction(
    () => (document.getElementById('device-link-code')?.textContent || '').length > 0,
    { timeout: 10000 }).catch(() => fail('primary never received a pairing code'));
  const code = await pageP.evaluate(() => document.getElementById('device-link-code').textContent.trim());

  // ── New device (separate context = separate identity/storage) ──
  step = 'newdevice-load';
  const ctxN = await browser.createBrowserContext();
  const pageN = await ctxN.newPage();
  const nerr = [];
  pageN.on('pageerror', e => nerr.push('newdevice pageerror: ' + e.message));
  await pageN.goto(URL, { waitUntil: 'load', timeout: 20000 });
  await online(pageN).catch(() => fail('new device never went online'));
  const newDid = await pageN.evaluate(() => localStorage.getItem('proxion_identity_did'));
  if (!newDid) fail('new device has no clientDid');
  if (newDid === primaryDid) fail('contexts are not isolated (same clientDid)');

  step = 'newdevice-submit';
  await pageN.evaluate(() => document.getElementById('ob-link-device').click());
  await pageN.evaluate((c) => {
    document.getElementById('pair-code-input').value = c;
    document.getElementById('pair-device-submit').click();
  }, code);

  // ── Primary sees the request, safety codes match, approve ──
  step = 'primary-approve';
  await pageP.waitForFunction(
    () => getComputedStyle(document.getElementById('device-link-approve-row')).display !== 'none',
    { timeout: 10000 }).catch(() => fail('primary never received the pairing request'));
  const safetyP = await pageP.evaluate(() => document.getElementById('device-link-safety').textContent.trim());
  const safetyN = await pageN.evaluate(() => document.getElementById('pair-device-safety').textContent.trim());
  if (!safetyP || safetyP !== safetyN) fail(`safety codes mismatch: primary=${safetyP} newdevice=${safetyN}`);
  await pageP.evaluate(() => document.getElementById('device-link-approve').click());

  // ── New device stores the cert and reloads; wait for it to reconnect ──
  step = 'newdevice-relink';
  await pageN.waitForFunction(
    () => !!localStorage.getItem('proxion_delegation_cert'), { timeout: 10000 })
    .catch(() => fail('new device never stored the delegation cert'));
  const certAccount = await pageN.evaluate(() => {
    try { return JSON.parse(localStorage.getItem('proxion_delegation_cert')).account_did; } catch { return null; }
  });
  if (certAccount !== primaryDid) fail(`cert account_did ${certAccount} != primary ${primaryDid}`);
  // It reloads ~1.2s after approval; wait for the reload to settle + reconnect.
  await new Promise(r => setTimeout(r, 2000));
  await online(pageN).catch(() => fail('new device did not reconnect after relink'));

  // ── Primary refreshes Linked Devices; the new device must appear ──
  step = 'assert-linked-list';
  await pageP.evaluate(() => document.getElementById('settings-btn').click());
  const prefix = newDid.slice(0, 16);
  await pageP.waitForFunction(
    (p) => (document.getElementById('settings-devices-list')?.textContent || '').includes(p),
    { timeout: 10000 }, prefix)
    .catch(() => fail(`new device (${prefix}…) never appeared in the primary's Linked Devices`));

  step = 'done';
  const errs = [...perr, ...nerr];
  if (errs.length) { console.error('  page errors:\n   - ' + errs.join('\n   - ')); process.exitCode = 1; }
  if (!process.exitCode) {
    console.log(`  ✓ pairing OK — new device linked to account ${primaryDid.slice(0, 20)}…, `
      + `safety code ${safetyP} matched, appears in Linked Devices.`);
  }
} catch (e) {
  console.error(`  ✗ [${step}] threw: ${e.message}`);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  if (gw) { try { gw.kill(); } catch { /* already gone */ } }
  if (dataDir) { try { rmSync(dataDir, { recursive: true, force: true }); } catch { /* best effort */ } }
}
