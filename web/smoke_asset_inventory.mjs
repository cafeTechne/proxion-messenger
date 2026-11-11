// Real CSS + browser and owner gateway: read-only retained-room/legacy review.
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import { startCss, provisionAccount, makeAuthSession, freePort } from './scripts/css-harness.mjs';
import { buildAndServeHttps, signIn } from './scripts/e2e-signin.mjs';
import { skipSmoke } from './scripts/smoke-status.mjs';

const HERE = fileURLToPath(new URL('.', import.meta.url)), REPO = resolve(HERE, '..');
const chrome = [process.env.PROXION_CHROME, '/usr/bin/google-chrome', '/usr/bin/chromium',
  'C:/Program Files/Google/Chrome/Application/chrome.exe', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
  .filter(Boolean).find(existsSync);
if (!chrome) skipSmoke('Chrome unavailable; set PROXION_CHROME.');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, css, served, gateway, dataDir, step = 'setup';
async function startGateway(account) {
  const httpPort = await freePort(), wsPort = await freePort();
  dataDir = mkdtempSync(join(tmpdir(), 'proxion-inventory-'));
  gateway = spawn('python', ['scripts/run_test_gateway.py'], { cwd: REPO, env: { ...process.env,
    PROXION_DATA_DIR: dataDir, PROXION_HTTP_PORT: String(httpPort), PROXION_WS_PORT: String(wsPort),
    PROXION_HOST: '127.0.0.1', PROXION_PUBLIC_URL: '', PROXION_REQUIRE_AUTH: '0',
    PROXION_CSS_URL: css.url, PROXION_CSS_EMAIL: account.email, PROXION_CSS_PASSWORD: account.password,
    PROXION_ALLOW_PRIVATE_RELAY: '1', PROXION_WEB_DIR: HERE,
    PYTHONPATH: join(REPO, 'proxion-messenger-core', 'src'),
  } });
  let log = '';
  gateway.stdout.on('data', d => { log += d; }); gateway.stderr.on('data', d => { log += d; });
  const url = `http://127.0.0.1:${httpPort}/`, deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    if (gateway.exitCode !== null) throw Error('gateway exited: ' + log.slice(-1000));
    if (/PROXION_GATEWAY_READY/.test(log) && await fetch(url).then(r => r.ok).catch(() => false)) return url;
    await sleep(300);
  }
  throw Error('gateway timeout: ' + log.slice(-1000));
}
async function seed(account) {
  const session = await makeAuthSession(account), base = account.storageRoot + 'proxion/rooms/retained/';
  // No descriptor/type-index entry: retained storage must remain discoverable.
  const items = Array.from({ length: 51 }, (_, i) => [`asset-seed-${String(i).padStart(2, '0')}.json`, '{}', 'application/json']);
  items.push(['files/note.webm', 'voice-fixture', 'audio/webm'], ['files/message/old.txt', 'legacy-fixture', 'text/plain']);
  for (const [name, body, type] of items) {
    const response = await session.fetch(base + name, { method: 'PUT', headers: { 'Content-Type': type }, body });
    assert(response.ok, `seed ${name}: ${response.status}`);
  }
}
async function review(page, label) {
  await page.click('#settings-btn');
  await page.click('[data-settings-target="settings-sec-pod"]');
  await page.click('#asset-inventory-refresh');
  await page.waitForFunction(() => [...document.querySelectorAll('#asset-inventory-rooms option')].some(o => o.value === 'retained'));
  await page.select('#asset-inventory-rooms', 'retained');
  await page.waitForFunction(() => document.querySelectorAll('#asset-inventory-results tbody tr').length === 50);
  assert.match(await page.$eval('#asset-inventory-results', el => el.textContent), /Reference not verified/);
  await page.click('#asset-inventory-next');
  await page.waitForFunction(() => document.querySelectorAll('#asset-inventory-results tbody tr').length === 2);
  await page.click('[data-asset-inventory-path="files/"]');
  await page.waitForFunction(() => document.querySelector('#asset-inventory-results')?.textContent.includes('Legacy voice copy'));
  await page.click('[data-asset-inventory-path="files/message/"]');
  await page.waitForFunction(() => document.querySelector('#asset-inventory-results')?.textContent.includes('old.txt'));
  assert.match(await page.$eval('#asset-inventory-results', el => el.textContent), /14 bytes/);
  await page.click('#asset-inventory-up');
  await page.waitForFunction(() => document.getElementById('asset-inventory-path').textContent === 'retained/files/');
  // Escape closes Settings, and reopening must clear any old inventory results.
  await page.keyboard.press('Escape');
  await page.click('#settings-btn');
  assert.equal(await page.$eval('#asset-inventory-results', el => el.childElementCount), 0);
  console.log(`✓ ${label}: retained room discovered, metadata pagination, legacy folders, close/reset`);
}
try {
  served = await buildAndServeHttps(HERE, join(HERE, 'scripts/build-web.mjs'));
  if (!served) skipSmoke('HTTPS build unavailable.');
  css = await startCss({ allowSelfSignedClientId: true });
  const account = await provisionAccount(css.url, { email: `inventory-${Date.now()}@test.example`, password: 'pw-12345678', label: 'inventory' });
  await seed(account);
  browser = await puppeteer.launch({ executablePath: chrome, headless: 'new', args: ['--no-sandbox', '--disable-gpu', '--ignore-certificate-errors'] });
  const errors = [];
  const web = await (await browser.createBrowserContext()).newPage();
  web.on('pageerror', e => errors.push(e.message));
  step = 'browser signin';
  await signIn(web, { appOrigin: served.origin, cssUrl: css.url, cookies: account.cookies });
  const reads = [];
  web.on('request', r => { if (r.url().includes('/proxion/rooms/retained/')) reads.push({ method: r.method(), url: r.url() }); });
  step = 'browser review'; await review(web, 'Browser');
  assert(reads.some(r => r.method === 'HEAD' && r.url.endsWith('/old.txt')));
  assert(reads.every(r => ['GET', 'HEAD', 'OPTIONS'].includes(r.method)));
  assert(reads.filter(r => r.method === 'GET').every(r => r.url.endsWith('/')), JSON.stringify(reads.filter(r => r.method === 'GET' && !r.url.endsWith('/'))));
  step = 'gateway startup';
  const url = await startGateway(account);
  const desktop = await (await browser.createBrowserContext()).newPage();
  desktop.on('pageerror', e => errors.push(e.message));
  await desktop.evaluateOnNewDocument(() => {
    localStorage.setItem('proxion_display_name', 'Inventory'); localStorage.setItem('proxion_pod_setup_skipped', '1');
  });
  const requests = [], cdp = await desktop.createCDPSession(); await cdp.send('Network.enable');
  cdp.on('Network.webSocketFrameSent', e => {
    try { const m = JSON.parse(e.response.payloadData); if (m.cmd === 'pod_fetch' && m.url.includes('/proxion/rooms/retained/')) requests.push(m); } catch { /* unrelated frame */ }
  });
  await desktop.goto(url, { waitUntil: 'load' });
  await desktop.evaluate(() => { const m = document.getElementById('onboarding-modal'); if (m) m.style.display = 'none'; });
  await desktop.waitForFunction(() => window.proxionCrossMode?.isReady(), { timeout: 60000 });
  requests.length = 0;
  step = 'desktop review'; await review(desktop, 'Desktop bridge');
  assert(requests.some(r => r.method === 'HEAD' && r.url.endsWith('/old.txt')));
  assert(requests.every(r => r.method === 'HEAD' || (r.method === 'GET' && r.url.endsWith('/'))));
  assert.deepEqual(errors, []);
  console.log('✓ Both clients used directory GET and file HEAD only; no binary downloads or mutations');
} catch (error) {
  console.error(`✗ ${step}:`, error); process.exitCode = 1;
} finally {
  await browser?.close(); served?.server.close();
  if (gateway) {
    const exited = new Promise(resolve => gateway.once('exit', resolve)); gateway.kill();
    await Promise.race([exited, sleep(2000)]);
  }
  css?.stop(); if (dataDir) rmSync(dataDir, { recursive: true, force: true });
}
