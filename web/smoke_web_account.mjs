// Account lifecycle in the browser build, against a live CSS pod: a new account
// with no name is asked for one, the name shows in the header and survives a
// reload, "Sign out of pod" really signs out, a second account signed in on
// the same browser shows its own name (not the first's), and a second device
// signing in as the first account gets its name back from the pod profile.
//
//   node web/smoke_web_account.mjs
// Needs Chrome (or PROXION_CHROME), openssl, and npx (for CSS). Self-skips if
// the cert or CSS cannot be produced.

import { existsSync } from 'fs';
import { resolve, join } from 'path';
import { fileURLToPath } from 'url';
import puppeteer from 'puppeteer-core';
import { startCss, provisionAccount } from './scripts/css-harness.mjs';
import { buildAndServeHttps, signIn } from './scripts/e2e-signin.mjs';

const HERE = resolve(fileURLToPath(new URL('.', import.meta.url)));
const CHROME = [
  process.env.PROXION_CHROME,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
].filter(Boolean).find((p) => p && existsSync(p));
if (!CHROME) { console.error('No Chrome/Edge found; set PROXION_CHROME.'); process.exit(2); }

let step = 'init';
const fail = (m) => { console.error(`  \u2717 [${step}] ${m}`); process.exitCode = 1; };
const ok = (m) => console.log(`  \u2713 ${m}`);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let browser = null, served = null, css = null;
async function cleanup() {
  try { if (browser) await browser.close(); } catch { /**/ }
  try { if (served) served.server.close(); } catch { /**/ }
  try { if (css) css.stop(); } catch { /**/ }
}

const header = (page) => page.evaluate(() => document.getElementById('username')?.textContent.trim());

// Drop every cookie for the pod host so the next OIDC login starts clean (the
// provider would otherwise remember the previous account's session).
async function clearPodCookies(page, cssUrl) {
  const cookies = await page.cookies(cssUrl);
  if (cookies.length) await page.deleteCookie(...cookies);
}

try {
  step = 'serve';
  served = await buildAndServeHttps(HERE, join(HERE, 'scripts', 'build-web.mjs'));
  if (!served) { console.log('  \u2013 skipped: could not build/serve over https (openssl?).'); await cleanup(); process.exit(0); }
  ok('built + serving the web app over https');

  step = 'css';
  css = await startCss({ allowSelfSignedClientId: true }).catch(() => null);
  if (!css) { console.log('  \u2013 skipped: no CSS pod available (offline / npx blocked).'); await cleanup(); process.exit(0); }
  const alice = await provisionAccount(css.url, { email: `alice-${Date.now()}@test.example`, password: 'pw-12345678', label: 'alice' });
  const bob = await provisionAccount(css.url, { email: `bob-${Date.now()}@test.example`, password: 'pw-12345678', label: 'bob' });
  ok('CSS pod up + two accounts provisioned');

  browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--disable-gpu', '--ignore-certificate-errors'] });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));

  // ── A new account is asked for a name; it shows and survives a reload ──
  step = 'name prompt';
  const asked = await signIn(page, { appOrigin: served.origin, cssUrl: css.url, cookies: alice.cookies, name: 'Alice Smoke' });
  if (asked) ok('a new account with no name is asked for one (Settings name field)');
  else fail('a new account with no name was not asked for one');
  await sleep(500);
  if ((await header(page)) === 'Alice Smoke') ok('the header shows the name just entered');
  else fail(`header after naming: ${JSON.stringify(await header(page))}`);
  await sleep(2000);   // the name is written to the pod profile in the background

  step = 'reload';
  await page.reload({ waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForFunction(() => typeof window.proxionWebDm === 'object', { timeout: 20000 });
  const reloaded = await page.evaluate(async () => {
    await window.proxionNameCheck;
    return { header: document.getElementById('username')?.textContent.trim(), settings: document.getElementById('settings-modal').style.display };
  });
  if (reloaded.header === 'Alice Smoke' && reloaded.settings !== 'flex') ok('after reload the header shows the name and nothing asks again');
  else fail(`after reload: ${JSON.stringify(reloaded)}`);

  // ── Sign out of pod actually signs out ──
  step = 'sign out';
  await page.evaluate(() => document.getElementById('settings-btn').click());
  await page.evaluate(() => document.getElementById('settings-pod-logout-btn').click());
  await page.waitForSelector('#confirm-ok', { visible: true, timeout: 5000 });
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 20000 }).catch(() => null),
    page.click('#confirm-ok'),
  ]);
  await sleep(1500);
  const out = await page.evaluate(() => ({
    signin: getComputedStyle(document.getElementById('web-signin-modal')).display,
    engines: typeof window.proxionWebDm,
    name: localStorage.getItem('proxion_display_name'),
    webid: localStorage.getItem('proxion_pod_webid'),
  }));
  if (out.signin !== 'none' && out.engines === 'undefined' && !out.name && !out.webid) ok('sign out reloads to the sign-in screen and forgets the account');
  else fail(`after sign out: ${JSON.stringify(out)}`);

  // ── A second account on the same browser shows its own name ──
  step = 'second account';
  await clearPodCookies(page, css.url);
  const askedBob = await signIn(page, { appOrigin: served.origin, cssUrl: css.url, cookies: bob.cookies });
  const bobState = await page.evaluate(() => ({
    header: document.getElementById('username')?.textContent.trim(),
    field: document.getElementById('settings-display-name').value,
    stored: localStorage.getItem('proxion_display_name'),
  }));
  if (askedBob && bobState.header !== 'Alice Smoke' && !bobState.field && bobState.stored !== 'Alice Smoke') {
    ok("the second account does not inherit the first account's name, and is asked for its own");
  } else fail(`second account: asked=${askedBob} ${JSON.stringify(bobState)}`);

  // ── Another device signing in as the first account restores its name ──
  step = 'second device';
  const ctx2 = await browser.createBrowserContext();
  const page2 = await ctx2.newPage();
  page2.on('pageerror', (e) => errors.push('device2 pageerror: ' + e.message));
  const asked2 = await signIn(page2, { appOrigin: served.origin, cssUrl: css.url, cookies: alice.cookies });
  await sleep(300);
  const h2 = await header(page2);
  if (!asked2 && h2 === 'Alice Smoke') ok('a second device gets the name back from the pod profile and shows it in the header');
  else fail(`second device: asked=${asked2} header=${JSON.stringify(h2)}`);

  if (errors.length) fail('page errors:\n    ' + errors.slice(0, 8).join('\n    '));
  else ok('no uncaught page errors');

  console.log(process.exitCode ? '\nSMOKE FAILED' : '\nSMOKE PASSED');
} catch (e) {
  fail(e && e.stack ? e.stack : String(e));
  console.log('\nSMOKE FAILED');
} finally {
  await cleanup();
}
