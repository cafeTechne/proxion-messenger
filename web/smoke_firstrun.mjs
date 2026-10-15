// First-run smoke: a stranger opening the app for the first time. Walks the
// onboarding wizard (including a failing pod test, which must reach the
// gateway), finishes setup, exercises the settings controls and checks they
// survive a reload, and follows an invite link into the Add contact dialog.
// Fails on any uncaught page error or console error.
//
//   node web/smoke_firstrun.mjs
//   PROXION_SMOKE_URL=https://127.0.0.1:PORT/ node web/smoke_firstrun.mjs
import { existsSync, readFileSync } from 'fs';
import { resolve } from 'path';
import { fileURLToPath } from 'url';
import puppeteer from 'puppeteer-core';
import { smokeGateway } from './scripts/gw-harness.mjs';

setTimeout(() => { console.error('HARD TIMEOUT'); process.exit(3); }, 150000);

const CHROME = [
  process.env.PROXION_CHROME,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
].filter(Boolean).find((p) => existsSync(p));
if (!CHROME) { console.error('No Chrome/Edge found; set PROXION_CHROME.'); process.exit(2); }

const REPO = resolve(fileURLToPath(new URL('..', import.meta.url)));
const VERSION = readFileSync(resolve(REPO, 'version.txt'), 'utf8').trim();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// The gateway's NAT/WebSocket chatter is environment noise, not a page bug.
const IGNORE = /WebSocket|Failed to load resource/;

const failures = [];
const check = (ok, what) => { console.log(`${ok ? '  ok ' : '  FAIL'} ${what}`); if (!ok) failures.push(what); };

const gw = await smokeGateway();
const browser = await puppeteer.launch({
  executablePath: CHROME, headless: 'new',
  args: ['--ignore-certificate-errors', '--no-sandbox', '--disable-gpu'],
});
const errors = [];
function watch(page, tag) {
  page.on('pageerror', (e) => errors.push(`${tag} pageerror: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error' && !IGNORE.test(m.text())) errors.push(`${tag} console: ${m.text()}`); });
  page.on('dialog', (d) => { errors.push(`${tag} native dialog: ${d.message()}`); d.dismiss(); });
}
const shownStep = (page) => page.evaluate(() =>
  [1, 2, 4, 5, 6].find((n) => document.getElementById(`ob-step-${n}`)?.style.display !== 'none'));
const display = (page, id) => page.$eval(`#${id}`, (e) => getComputedStyle(e).display);

try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  watch(page, 'main');
  await page.goto(gw.url, { waitUntil: 'load', timeout: 20000 });
  await page.waitForFunction(() => getComputedStyle(document.getElementById('onboarding-modal')).display !== 'none', { timeout: 15000 });

  console.log('onboarding');
  check(await shownStep(page) === 1, 'wizard opens on the welcome step');
  await page.click('#ob-start-btn');
  check(await shownStep(page) === 2, 'Get started goes to the name step');
  await page.click('#ob-step2-btn');
  check((await page.$eval('#ob-name-error', (e) => e.textContent)).trim() !== '', 'an empty name is explained');
  await page.type('#ob-name', 'Alice');
  await page.click('#ob-step2-btn');
  check(await shownStep(page) === 4, 'a name goes on to the pod step');

  // Pod test against a server that is not there: the gateway must answer with
  // a message naming the URL (the request used to go to a hardcoded port).
  await page.click('.ob-provider-card[data-css="custom"]');
  await page.type('#ob-pod-css-url', 'http://127.0.0.1:1');
  await page.type('#ob-pod-email', 'alice@example.org');
  await page.type('#ob-pod-password', 'not-a-real-password');
  await page.click('#ob-pod-test-btn');
  await page.waitForFunction(() => !document.getElementById('ob-pod-test-btn').disabled, { timeout: 20000 });
  const podStatus = await page.$eval('#ob-pod-status', (e) => e.textContent);
  check(podStatus.includes('127.0.0.1:1'), `pod test reaches the gateway ("${podStatus}")`);
  await page.click('#ob-pod-back-btn');
  check(await display(page, 'ob-pod-providers') !== 'none', 'back returns to the provider list');

  await page.click('#ob-skip-pod');
  await page.waitForSelector('#confirm-ok', { visible: true });
  await page.click('#confirm-ok');
  await sleep(200);
  check(await shownStep(page) === 5, 'start without a pod goes to the room step');
  await page.click('#ob-step5-join');
  check((await page.$eval('#ob-join-error', (e) => e.textContent)).trim() !== '', 'an empty invite code is explained');
  await page.click('#ob-step5-skip');
  check(await shownStep(page) === 6, 'skip goes to the last step');
  await page.waitForFunction(() => document.getElementById('ob-my-addr').textContent.includes('did:key:'), { timeout: 10000 });
  check(true, 'the last step shows the Proxion address');
  await page.click('#ob-finish-btn');
  await sleep(300);
  check(await display(page, 'onboarding-modal') === 'none', 'Open Proxion closes the wizard');

  console.log('settings');
  await page.click('#settings-btn');
  await page.waitForSelector('#settings-modal', { visible: true });
  const navTargets = await page.$$eval('.settings-nav-item', (ns) => ns.map((n) => n.dataset.settingsTarget));
  for (const target of navTargets) {
    await page.click(`.settings-nav-item[data-settings-target="${target}"]`);
    const shown = await page.$eval(`#${target}`, (s) => s.offsetParent !== null);
    if (!shown) check(false, `settings nav shows ${target}`);
  }
  check(navTargets.length >= 8, `every settings section is reachable (${navTargets.length})`);
  await page.waitForFunction((v) => document.getElementById('settings-app-version').textContent.trim() === v, { timeout: 5000 }, VERSION)
    .then(() => check(true, `About shows version ${VERSION}`))
    .catch(async () => check(false, `About shows version ${VERSION} (got "${await page.$eval('#settings-app-version', (e) => e.textContent)}")`));
  await page.select('#settings-theme', 'light');
  await page.click('#settings-receipts-toggle');
  await page.click('#settings-link-previews-toggle');
  await page.$eval('#settings-display-name', (e) => { e.value = ''; });
  await page.type('#settings-display-name', 'Alice Two');
  await page.click('#settings-save-btn');
  await sleep(500);
  check(await display(page, 'settings-modal') === 'none', 'Done closes settings');

  await page.reload({ waitUntil: 'load' });
  await sleep(2500);
  check(await display(page, 'onboarding-modal') === 'none', 'the wizard stays done after a reload');
  await page.click('#settings-btn');
  await page.waitForSelector('#settings-modal', { visible: true });
  const kept = await page.evaluate(() => ({
    theme: document.getElementById('settings-theme').value,
    receipts: document.getElementById('settings-receipts-toggle').checked,
    previews: document.getElementById('settings-link-previews-toggle').checked,
    name: document.getElementById('settings-display-name').value,
  }));
  check(kept.theme === 'light' && !kept.receipts && kept.previews && kept.name === 'Alice Two',
    `settings survive a reload (${JSON.stringify(kept)})`);
  await page.keyboard.press('Escape');

  console.log('invite link');
  const ctx = await browser.createBrowserContext();
  const fresh = await ctx.newPage();
  await fresh.setViewport({ width: 1280, height: 900 });
  watch(fresh, 'invitee');
  const inviter = 'did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK@https://friend.example';
  await fresh.goto(`${gw.url}?from=${encodeURIComponent(inviter)}`, { waitUntil: 'load' });
  await fresh.waitForSelector('#deeplink-confirm-ok', { visible: true, timeout: 15000 });
  await fresh.click('#deeplink-confirm-ok');
  await sleep(200);
  const added = await fresh.evaluate(() => ({
    open: getComputedStyle(document.getElementById('add-peer-modal')).display !== 'none',
    value: document.getElementById('add-peer-input').value,
  }));
  check(added.open && added.value === inviter, 'confirming an invite link fills the Add contact dialog');
  await ctx.close();
} finally {
  await browser.close();
  await gw.stop();
}

if (errors.length) {
  console.error('\npage errors:');
  errors.forEach((e) => console.error('  ' + e));
}
if (failures.length || errors.length) {
  console.error(`\nfirst-run smoke FAILED (${failures.length} checks, ${errors.length} page errors)`);
  process.exit(1);
}
console.log('\nfirst-run smoke passed');
process.exit(0);
